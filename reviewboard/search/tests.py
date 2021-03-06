from __future__ import unicode_literals

import django
import haystack
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core.urlresolvers import reverse
from django.utils import six
from django.utils.six.moves.urllib.parse import urlencode
from djblets.siteconfig.models import SiteConfiguration
from djblets.testing.decorators import add_fixtures
from kgb import SpyAgency

try:
    # Django >= 1.7
    from django.apps.registry import apps
except ImportError:
    # Django 1.6
    apps = None

from reviewboard.admin.server import build_server_url
from reviewboard.admin.siteconfig import load_site_config
from reviewboard.reviews.models import ReviewRequestDraft
from reviewboard.search.signal_processor import SignalProcessor
from reviewboard.search.testing import reindex_search
from reviewboard.site.urlresolvers import local_site_reverse
from reviewboard.testing.testcase import TestCase


class SignalProcessorTests(SpyAgency, TestCase):
    """Unit tetss for reviewboard.search.signal_processor.SignalProcessor."""

    def test_can_process_signals_with_siteconfig(self):
        """Testing SignalProcessor.can_process_signals with stored
        SiteConfiguration
        """
        self.assertIsNotNone(SiteConfiguration.objects.get_current())

        signal_processor = self._create_signal_processor()
        self.assertTrue(signal_processor.can_process_signals)

    def test_can_process_signals_without_siteconfig(self):
        """Testing SignalProcessor.can_process_signals without stored
        SiteConfiguration
        """
        def _get_siteconfig(*args, **kwargs):
            raise SiteConfiguration.DoesNotExist

        self.spy_on(SiteConfiguration.objects.get_current,
                    call_fake=_get_siteconfig)

        signal_processor = self._create_signal_processor()
        self.assertFalse(signal_processor.can_process_signals)

        # Make sure it works once one has been created.
        SiteConfiguration.objects.get_current.unspy()
        self.assertTrue(signal_processor.can_process_signals)

    def _create_signal_processor(self):
        """Return a new instance of our Haystack signal processor.

        Returns:
            reviewboard.search.signal_processor.SignalProcessor:
            The new signal processor.
        """
        return SignalProcessor(haystack.connections,
                               haystack.connection_router)


class SearchTests(SpyAgency, TestCase):
    """Unit tests for search functionality."""

    fixtures = ['test_users']

    @classmethod
    def setUpClass(cls):
        """Set some initial state for all search-related tests.

        This will enable search and reset Haystack's configuration based
        on that, allowing the search tests to run.
        """
        super(SearchTests, cls).setUpClass()

        siteconfig = SiteConfiguration.objects.get_current()
        cls._old_search_enabled = siteconfig.get('search_enable')

        siteconfig.set('search_enable', True)
        siteconfig.save()

        load_site_config()

    @classmethod
    def tearDownClass(cls):
        super(SearchTests, cls).tearDownClass()

        siteconfig = SiteConfiguration.objects.get_current()
        siteconfig.set('search_enable', cls._old_search_enabled)
        siteconfig.save()

        load_site_config()

    def test_search_all(self):
        """Testing search with review requests and users"""
        # We already have doc. Now let's create a review request.
        review_request = self.create_review_request(submitter='doc',
                                                    publish=True)
        reindex_search()

        # Perform the search.
        response = self.search('doc')
        context = response.context
        self.assertEqual(context['hits_returned'], 2)

        results = context['object_list']
        self.assertEqual(results[0].content_type(), 'auth.user')
        self.assertEqual(results[0].username, 'doc')
        self.assertEqual(results[1].content_type(), 'reviews.reviewrequest')
        self.assertEqual(results[1].summary, review_request.summary)

    def test_filter_review_requests(self):
        """Testing search with filtering for review requests"""
        # We already have doc. Now let's create a review request.
        review_request = self.create_review_request(submitter='doc',
                                                    publish=True)
        reindex_search()

        # Perform the search.
        response = self.search('doc', filter_by='reviewrequests')
        context = response.context
        self.assertEqual(context['hits_returned'], 1)

        results = context['object_list']
        self.assertEqual(results[0].content_type(), 'reviews.reviewrequest')
        self.assertEqual(results[0].summary, review_request.summary)

    def test_filter_users(self):
        """Testing search with filtering for review requests"""
        # We already have doc. Now let's create a review request.
        self.create_review_request(submitter='doc', publish=True)
        reindex_search()

        # Perform the search.
        response = self.search('doc', filter_by='users')
        context = response.context
        self.assertEqual(context['hits_returned'], 1)

        results = context['object_list']
        self.assertEqual(results[0].content_type(), 'auth.user')
        self.assertEqual(results[0].username, 'doc')

    @add_fixtures(['test_scmtools'])
    def test_review_requests_without_private_repo_access(self):
        """Testing search with private review requests without access to
        private repositories
        """
        self.client.login(username='grumpy', password='grumpy')
        user = User.objects.get(username='grumpy')

        repository = self.create_repository(public=False)
        review_request = self.create_review_request(repository=repository,
                                                    publish=True)
        self.assertFalse(review_request.is_accessible_by(user))
        reindex_search()

        # Perform the search.
        response = self.search(review_request.summary)
        context = response.context
        self.assertEqual(context['hits_returned'], 0)

    @add_fixtures(['test_scmtools'])
    def test_review_requests_with_private_repo_access(self):
        """Testing search with private review requests with access to
        private repositories
        """
        self.client.login(username='grumpy', password='grumpy')
        user = User.objects.get(username='grumpy')

        repository = self.create_repository(public=False)
        repository.users.add(user)
        review_request = self.create_review_request(repository=repository,
                                                    publish=True)

        self.assertTrue(review_request.is_accessible_by(user))
        reindex_search()

        # Perform the search.
        response = self.search(review_request.summary)
        context = response.context
        self.assertEqual(context['hits_returned'], 1)

        results = context['object_list']
        self.assertEqual(results[0].content_type(), 'reviews.reviewrequest')
        self.assertEqual(results[0].summary, review_request.summary)

    @add_fixtures(['test_scmtools'])
    def test_review_requests_with_private_repo_access_through_group(self):
        """Testing search with private review requests with access to
        private repositories through groups
        """
        self.client.login(username='grumpy', password='grumpy')
        user = User.objects.get(username='grumpy')

        group = self.create_review_group(invite_only=True)
        group.users.add(user)

        repository = self.create_repository(public=False)
        repository.review_groups.add(group)
        review_request = self.create_review_request(repository=repository,
                                                    publish=True)

        self.assertTrue(review_request.is_accessible_by(user))
        reindex_search()

        # Perform the search.
        response = self.search(review_request.summary)
        context = response.context
        self.assertEqual(context['hits_returned'], 1)

        results = context['object_list']
        self.assertEqual(results[0].content_type(), 'reviews.reviewrequest')
        self.assertEqual(results[0].summary, review_request.summary)

    def test_review_requests_without_private_group_access(self):
        """Testing search with private review requests without access to
        a private group
        """
        self.client.login(username='grumpy', password='grumpy')
        user = User.objects.get(username='grumpy')

        group = self.create_review_group(invite_only=True)

        review_request = self.create_review_request(publish=True)
        review_request.target_groups.add(group)

        self.assertFalse(review_request.is_accessible_by(user))
        reindex_search()

        # Perform the search.
        response = self.search(review_request.summary)
        context = response.context
        self.assertEqual(context['hits_returned'], 0)

    def test_review_requests_with_private_group_access(self):
        """Testing search with private review requests with access to
        a private group
        """
        self.client.login(username='grumpy', password='grumpy')
        user = User.objects.get(username='grumpy')

        group = self.create_review_group(invite_only=True)
        group.users.add(user)

        review_request = self.create_review_request(publish=True)
        review_request.target_groups.add(group)

        self.assertTrue(review_request.is_accessible_by(user))
        reindex_search()

        # Perform the search.
        response = self.search(review_request.summary)
        context = response.context
        self.assertEqual(context['hits_returned'], 1)

        results = context['object_list']
        self.assertEqual(results[0].content_type(), 'reviews.reviewrequest')
        self.assertEqual(results[0].summary, review_request.summary)

    @add_fixtures(['test_scmtools'])
    def test_review_requests_with_private_repo_access_no_private_group(self):
        """Testing search with private review requests with access to
        a private repository and without access to a private_group
        """
        self.client.login(username='grumpy', password='grumpy')
        user = User.objects.get(username='grumpy')

        group = self.create_review_group(invite_only=True)

        repository = self.create_repository(public=False)
        repository.users.add(user)

        review_request = self.create_review_request(repository=repository,
                                                    publish=True)
        review_request.target_groups.add(group)

        self.assertFalse(review_request.is_accessible_by(user))
        reindex_search()

        # Perform the search.
        response = self.search(review_request.summary)
        context = response.context
        self.assertEqual(context['hits_returned'], 0)

    @add_fixtures(['test_scmtools'])
    def test_review_requests_with_private_repository_as_submitter(self):
        """Testing search with private review requests without access to
        a private repository as the submitter
        """
        self.client.login(username='grumpy', password='grumpy')
        user = User.objects.get(username='grumpy')

        repository = self.create_repository(public=False)
        repository.users.add(user)

        review_request = self.create_review_request(repository=repository,
                                                    submitter=user,
                                                    publish=True)

        self.assertTrue(review_request.is_accessible_by(user))
        reindex_search()

        # Perform the search.
        response = self.search(review_request.summary)
        context = response.context
        self.assertEqual(context['hits_returned'], 1)

        results = context['object_list']
        self.assertEqual(results[0].content_type(), 'reviews.reviewrequest')
        self.assertEqual(results[0].summary, review_request.summary)

    @add_fixtures(['test_scmtools'])
    def test_review_requests_with_private_repository_and_target_people(self):
        """Testing search with private review requests without access to
        a private repository and user in target_people
        """
        self.client.login(username='grumpy', password='grumpy')
        user = User.objects.get(username='grumpy')

        repository = self.create_repository(public=False)
        review_request = self.create_review_request(repository=repository,
                                                    publish=True)
        review_request.target_people.add(user)

        self.assertFalse(review_request.is_accessible_by(user))
        reindex_search()

        # Perform the search.
        response = self.search(review_request.summary)
        context = response.context
        self.assertEqual(context['hits_returned'], 0)

    def test_review_requests_with_private_group_and_target_people(self):
        """Testing search with private review requests without access to
        a private group and user in target_people
        """
        self.client.login(username='grumpy', password='grumpy')
        user = User.objects.get(username='grumpy')
        group = self.create_review_group(invite_only=True)

        review_request = self.create_review_request(publish=True)
        review_request.target_groups.add(group)
        review_request.target_people.add(user)

        self.assertTrue(review_request.is_accessible_by(user))
        reindex_search()

        # Perform the search.
        response = self.search(review_request.summary)
        context = response.context
        self.assertEqual(context['hits_returned'], 1)

        results = context['object_list']
        self.assertEqual(results[0].content_type(), 'reviews.reviewrequest')
        self.assertEqual(results[0].summary, review_request.summary)

    def test_search_review_request_id(self):
        """Testing search with a review request ID"""
        site = Site.objects.get_current()
        site.domain = 'testserver'
        site.save()

        self.client.login(username='grumpy', password='grumpy')
        user = User.objects.get(username='grumpy')

        review_request = self.create_review_request(publish=True)

        self.assertTrue(review_request.is_accessible_by(user))
        reindex_search()

        # Perform the search.
        response = self.search('%d' % review_request.id)

        self.assertRedirects(response, review_request.get_absolute_url())

    def test_search_numeric_non_id(self):
        """Testing search with a numeric query that is not a review request
        ID
        """
        self.client.login(username='grumpy', password='grumpy')
        user = User.objects.get(username='grumpy')

        review_request = self.create_review_request(bugs_closed='123,456',
                                                    publish=True)

        self.assertTrue(review_request.is_accessible_by(user))
        reindex_search()

        # Perform the search.
        response = self.search('456')
        context = response.context
        self.assertEqual(context['hits_returned'], 1)

        results = context['object_list']
        self.assertEqual(results[0].content_type(), 'reviews.reviewrequest')
        self.assertEqual(results[0].summary, review_request.summary)

    def search(self, q, filter_by=None):
        """Perform a search with the given query and optional filters.

        The resulting response object is returned. The search results can be
        inspected by looking at ``response.context``.
        """
        options = {
            'q': q,
        }

        if filter_by:
            options['model_filter'] = filter_by

        return self.client.get(local_site_reverse('search'), options)

    def test_on_the_fly_indexing_review_requests(self):
        """Testing on-the-fly indexing for review requests"""
        reindex_search()

        signal_processor = self._get_signal_processor()

        group = self.create_review_group()
        invite_only_group = self.create_review_group(name='invite-only-group',
                                                     invite_only=True)

        grumpy = User.objects.get(username='grumpy')

        with self.siteconfig_settings({'search_on_the_fly_indexing': True},
                                      reload_settings=False):
            self.spy_on(signal_processor.handle_save)

            review_request = self.create_review_request(summary='foo',
                                                        publish=True)
            self.assertTrue(signal_processor.handle_save.spy.called)

            draft = ReviewRequestDraft.create(review_request)
            draft.summary = 'Not foo whatsoever'
            draft.save()
            draft.target_people = [grumpy]
            draft.target_groups = [group, invite_only_group]

            review_request.publish(review_request.submitter)

            rsp = self.search('Not foo')

        # There will be one call from each publish.
        self.assertEqual(len(signal_processor.handle_save.spy.calls), 2)
        self.assertEqual(rsp.context['hits_returned'], 1)

        result = rsp.context['result']
        self.assertEqual(result.summary, 'Not foo whatsoever')
        self.assertEqual(result.target_users, [six.text_type(grumpy.pk)])
        self.assertEqual(result.private_target_groups,
                         [six.text_type(invite_only_group.pk)])

    def test_on_the_fly_indexing_users(self):
        """Testing on-the-fly indexing for users"""
        reindex_search()

        signal_processor = self._get_signal_processor()

        u = User.objects.get(username='doc')

        group = self.create_review_group()
        invite_only_group = self.create_review_group(name='invite-only-group',
                                                     invite_only=True)

        with self.siteconfig_settings({'search_on_the_fly_indexing': True},
                                      reload_settings=False):
            self.spy_on(signal_processor.handle_save)

            u.username = 'not_doc'
            u.first_name = 'Not Doc'
            u.last_name = 'Dwarf'
            u.save()

            u.review_groups = [group, invite_only_group]
            invite_only_group.users = [u]

            rsp = self.search('not_doc')

        # Django's optimized things since this code was originally written.
        # It's a lot smarter when it comes to deciding whether clears/adds
        # are needed.
        #
        # On Django 1.6, there should be five calls:
        #
        #  * two from each of the m2m_changed actions post_clear and
        #    post_add
        #  * and one from User.save()
        #
        # On Django 1.11 (likely older ones as well), we only expect two:
        #
        #  * One for the User.save()
        #  * One for the m2m_changed (post_add) from saving the initial list
        #    of groups.
        if django.VERSION >= (1, 11):
            expected_calls = 2
        else:
            expected_calls = 5

        self.assertEqual(len(signal_processor.handle_save.spy.calls),
                         expected_calls)

        self.assertEqual(rsp.context['hits_returned'], 1)
        result = rsp.context['result']

        self.assertEqual(result.groups, 'test-group')
        self.assertEqual(result.url, '/users/not_doc/')
        self.assertEqual(result.username, 'not_doc')
        self.assertEqual(result.full_name, 'Not Doc Dwarf')

    def test_on_the_fly_indexing_profile(self):
        """Testing on-the-fly indexing for user profiles"""
        reindex_search()

        signal_processor = self._get_signal_processor()

        with self.siteconfig_settings({'search_on_the_fly_indexing': True},
                                      reload_settings=False):
            self.spy_on(signal_processor.handle_save)

            user = User.objects.get(username='doc')
            profile = user.get_profile()
            profile.is_private = True
            profile.save(update_fields=('is_private',))

            rsp = self.search('doc')

        self.assertEqual(len(signal_processor.handle_save.spy.calls), 1)

        self.assertEqual(rsp.context['hits_returned'], 1)
        result = rsp.context['result']

        self.assertEqual(result.groups, '')
        self.assertEqual(result.url, '/users/doc/')
        self.assertEqual(result.username, 'doc')
        self.assertEqual(result.full_name, '')

    def test_search_by_full_name_public_profile(self):
        """Testing searching by full name for users with public profiles"""
        user = User.objects.get(username='doc')
        self.create_review_request(submitter=user,
                                   publish=True)

        reindex_search()

        rsp = self.search('Doc Dwarf')
        self.assertEqual(rsp.context['hits_returned'], 2)

        actual_results = {
            key.id
            for key in rsp.context['page_obj']
        }

        expected_results = {
            'auth.user.2',
            'reviews.reviewrequest.1',
        }

        self.assertEqual(expected_results, actual_results)

    def test_search_by_full_name_private_profile(self):
        """Testing searching by full name for users with private profiles"""
        user = User.objects.get(username='doc')
        profile = user.get_profile()
        profile.is_private = True
        profile.save(update_fields=('is_private',))

        self.create_review_request(submitter=user, publish=True)

        reindex_search()

        rsp = self.search('Doc Dwarf')
        self.assertEqual(rsp.context['hits_returned'], 0)

    def test_search_by_name_public_profile(self):
        """Testing searching by first/last name with public profiles"""
        user = User.objects.get(username='doc')
        user.first_name = 'Doctor'
        user.save()
        self.create_review_request(submitter=user, publish=True)

        reindex_search()

        rsp = self.search('Doctor')
        self.assertEqual(rsp.context['hits_returned'], 2)

        actual_results = {
            key.id
            for key in rsp.context['page_obj']
        }

        expected_results = {
            'auth.user.2',
            'reviews.reviewrequest.1',
        }

        self.assertEqual(expected_results, actual_results)

        rsp = self.search('Dwarf')
        self.assertEqual(rsp.context['hits_returned'], 4)

        actual_results = {
            key.id
            for key in rsp.context['page_obj']
        }

        expected_results = {
            'auth.user.2',
            'auth.user.3',
            'auth.user.4',
            'reviews.reviewrequest.1',
        }

        self.assertEqual(expected_results, actual_results)

    def test_search_by_name_private_profile(self):
        """Testing searching by first/last name with private profiles"""
        user = User.objects.get(username='doc')
        user.first_name = 'Doctor'
        user.save()

        profile = user.get_profile()
        profile.is_private = True
        profile.save(update_fields=('is_private',))

        self.create_review_request(submitter=user, publish=True)

        reindex_search()

        rsp = self.search('Doctor')
        self.assertEqual(rsp.context['hits_returned'], 0)

        rsp = self.search('Dwarf')
        self.assertEqual(rsp.context['hits_returned'], 2)

        actual_results = {
            key.id
            for key in rsp.context['page_obj']
        }

        expected_results = {
            'auth.user.3',
            'auth.user.4',
        }

        self.assertEqual(expected_results, actual_results)

    def test_search_by_email_public_profile(self):
        """Testing searching by e-mail address with public profiles"""
        reindex_search()

        rsp = self.search('@example.com')
        self.assertEqual(rsp.context['hits_returned'], 4)

        actual_results = {
            key.id
            for key in rsp.context['page_obj']
        }

        expected_results = {
            'auth.user.1',
            'auth.user.2',
            'auth.user.3',
            'auth.user.4',
        }

        self.assertEqual(expected_results, actual_results)

    def test_search_by_email_private_profile(self):
        """Testing searching by e-mail address with private profiles"""
        user = User.objects.get(username='doc')

        profile = user.get_profile()
        profile.is_private = True
        profile.save(update_fields=('is_private',))

        reindex_search()

        rsp = self.search('@example.com')
        self.assertEqual(rsp.context['hits_returned'], 3)

        actual_results = {
            key.id
            for key in rsp.context['page_obj']
        }

        expected_results = {
            'auth.user.1',
            'auth.user.3',
            'auth.user.4',
        }

        self.assertEqual(expected_results, actual_results)

    def _get_signal_processor(self):
        """Return the configured signal processor.

        This will return correct the signal processor for the current
        version of Haystack. This is needed to provide both Django 1.6
        and 1.11 compatibility.

        Returns:
            reviewboard.search.signal_processor.SignalProcessor:
            The configured signal processor.
        """
        if apps is None:
            # Django == 1.6
            from haystack import signal_processor
        else:
            # Django >= 1.7
            app = apps.get_app_config('haystack')
            signal_processor = app.signal_processor

        return signal_processor


class ViewTests(TestCase):
    """Tests for the search view."""

    def test_get_enabled_no_query(self):
        """Testing the search view without a query redirects to all review
        requests
        """
        with self.siteconfig_settings({'search_enable': True},
                                      reload_settings=False):
            rsp = self.client.get(reverse('search'))

        self.assertRedirects(rsp, '/r/')

    def test_get_enabled_query(self):
        """Testing the search view with a query"""
        with self.siteconfig_settings({'search_enable': True},
                                      reload_settings=False):
            rsp = self.client.get(
                '%s?%s'
                % (reverse('search'),
                   urlencode({'q': 'foo'}))
            )

        self.assertEqual(rsp.status_code, 200)

        # Check for the search form.
        self.assertIn(b'<form method="get" action="/search/" role="search">',
                      rsp.content)

        # And the filtered search links.
        self.assertIn(
            b'<a href="?q=foo&model_filter=reviewrequests" rel="nofollow">',
            rsp.content)
        self.assertIn(
            b'<a href="?q=foo&model_filter=users" rel="nofollow">',
            rsp.content)

    def test_get_disabled(self):
        """Testing the search view with search disabled"""
        with self.siteconfig_settings({'search_enable': False},
                                      reload_settings=False):
            rsp = self.client.get(reverse('search'))

        self.assertEqual(rsp.status_code, 200)
        self.assertIn(
            b'<title>Indexed search not enabled',
            rsp.content)
        self.assertNotIn(b'<form', rsp.content)

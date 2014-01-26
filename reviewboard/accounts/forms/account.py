from __future__ import unicode_literals

from django import forms
from django.contrib import messages
from django.forms import widgets
from django.template.context import RequestContext
from django.template.loader import render_to_string
from django.utils.datastructures import SortedDict
from django.utils.translation import ugettext_lazy as _
from djblets.util.compat import six
from djblets.forms.fields import TimeZoneField
from djblets.siteconfig.models import SiteConfiguration

from reviewboard.accounts.backends import get_auth_backends
from reviewboard.reviews.models import Group
from reviewboard.site.urlresolvers import local_site_reverse


class AccountForm(forms.Form):
    """Base class for a form on the My Account page.

    AccountForms belong to AccountPages, and will be displayed on the
    My Account page for a user.

    A simple form presents fields that can be filled out and posted.
    More advanced forms can supply their own template or even their own
    JavaScript models and views.
    """
    form_id = None
    form_title = None

    save_label = _('Save')

    template_name = 'accounts/prefs_page_form.html'

    css_bundle_names = []
    js_bundle_names = []

    js_model_class = None
    js_view_class = None

    form_target = forms.CharField(
        required=False,
        widget=forms.HiddenInput)

    def __init__(self, page, request, user, *args, **kwargs):
        super(AccountForm, self).__init__(*args, **kwargs)
        self.page = page
        self.request = request
        self.user = user
        self.profile = user.get_profile()

        self.fields['form_target'].initial = self.form_id
        self.load()

    def set_initial(self, field_values):
        """Sets the initial fields for the form based on provided data.

        This can be used during load() to fill in the fields based on
        data from the database or another source.
        """
        for field, value in six.iteritems(field_values):
            self.fields[field].initial = value

    def is_visible(self):
        """Returns whether the form should be visible.

        This can be overridden to hide forms based on certain criteria.
        """
        return True

    def get_js_model_data(self):
        """Returns data to pass to the JavaScript Model during instantiation.

        If js_model_class is provided, the data returned from this function
        will be provided to the model when constructued.
        """
        return {}

    def get_js_view_data(self):
        """Returns data to pass to the JavaScript View during instantiation.

        If js_view_class is provided, the data returned from this function
        will be provided to the view when constructued.
        """
        return {}

    def render(self):
        """Renders the form."""
        return render_to_string(self.template_name,
                                RequestContext(self.request, {
            'form': self,
            'page': self.page,
        }))

    def load(self):
        """Loads data for the form.

        By default, this does nothing. Subclasses can override this to
        load data into the fields based on data from the database or
        from another source.
        """
        pass

    def save(self):
        """Saves the form data.

        Subclasses can override this to save data from the fields into
        the database.
        """
        raise NotImplementedError


class AccountSettingsForm(AccountForm):
    """Form for the Settings page for an account."""
    form_id = 'settings'
    form_title = _('Settings')
    save_label = _('Save Settings')

    timezone = TimeZoneField(
        label=_('Time zone'),
        required=True,
        help_text=_("The time zone you're in."))

    syntax_highlighting = forms.BooleanField(
        label=_('Enable syntax highlighting in the diff viewer'),
        required=False)
    open_an_issue = forms.BooleanField(
        label=_('Always open an issue when comment box opens'),
        required=False)

    def is_visible(self):
        backend = get_auth_backends()[0]

        return backend.supports_change_password

    def load(self):
        self.set_initial({
            'open_an_issue': self.profile.open_an_issue,
            'syntax_highlighting': self.profile.syntax_highlighting,
            'timezone': self.profile.timezone,
        })

        siteconfig = SiteConfiguration.objects.get_current()

        if not siteconfig.get('diffviewer_syntax_highlighting'):
            del self.fields['syntax_highlighting']

    def save(self):
        if 'syntax_highlighting' in self.cleaned_data:
            self.profile.syntax_highlighting = \
                self.cleaned_data['syntax_highlighting']

        self.profile.open_an_issue = self.cleaned_data['open_an_issue']
        self.profile.timezone = self.cleaned_data['timezone']
        self.profile.save()

        messages.add_message(self.request, messages.INFO,
                             _('Your settings have been saved.'))


class ChangePasswordForm(AccountForm):
    """Form for changing a user's password."""
    form_id = 'change_password'
    form_title = _('Change Password')
    save_label = _('Change Password')

    old_password = forms.CharField(
        label=_('Current password'),
        required=True,
        widget=widgets.PasswordInput())
    password1 = forms.CharField(
        label=_('New password'),
        required=True,
        widget=widgets.PasswordInput())
    password2 = forms.CharField(
        label=_('New password (confirm)'),
        required=True,
        widget=widgets.PasswordInput())

    def clean_old_password(self):
        backend = get_auth_backends()[0]

        password = self.cleaned_data['old_password']

        if not backend.authenticate(self.user.username, password):
            raise forms.ValidationError(_('This password is incorrect'))

    def clean_password2(self):
        p1 = self.cleaned_data['password1']
        p2 = self.cleaned_data['password2']

        if p1 != p2:
            raise forms.ValidationError(_('Passwords do not match'))

        return p2

    def save(self):
        backend = get_auth_backends()[0]
        backend.update_password(self.user, self.cleaned_data['password1'])
        self.user.save()

        messages.add_message(selfrequest, messages.INFO,
                             _('Your password has been changed.'))


class ProfileForm(AccountForm):
    """Form for the Profile page for an account."""
    form_id = 'profile'
    form_title = _('Profile')
    save_label = _('Save Profile')

    first_name = forms.CharField(
        label=_('First name'),
        required=False)
    last_name = forms.CharField(
        label=_('Last name'),
        required=False)
    email = forms.EmailField(
        label=_('E-mail address'),
        required=True)
    profile_private = forms.BooleanField(
        required=False,
        label=_("Keep this information private"))

    def load(self):
        self.set_initial({
            'first_name': self.user.first_name,
            'last_name': self.user.last_name,
            'email': self.user.email,
            'profile_private': self.profile.is_private,
        })

        backend = get_auth_backends()[0]

        if not backend.supports_change_name:
            del self.fields['first_name']
            del self.fields['last_name']

        if not backend.supports_change_email:
            del self.fields['email']

    def save(self):
        backend = get_auth_backends()[0]

        if not backend.supports_change_name:
            self.user.first_name = self.cleaned_data['first_name']
            self.user.last_name = self.cleaned_data['last_name']
            backend.update_name(self.user)

        if not backend.supports_change_email:
            new_email = self.cleaned_data['email']

            if new_email != self.user.email:
                self.user.email = new_email
                backend.update_email(self.user)

        self.user.save()

        self.profile.is_private = self.cleaned_data['profile_private']
        self.profile.save()

        messages.add_message(self.request, messages.INFO,
                             _('Your profile has been saved.'))


class GroupsForm(AccountForm):
    """Form for the group membership page.

    Unlike most forms, this doesn't deal with fields or saving to the database.
    Instead, it sets up the JavaScript View and provides serialized data
    representing the groups. The View handles group membership through the
    API.
    """
    form_id = 'groups'
    form_title = _('Groups')
    save_label = None

    js_view_class = 'RB.JoinedGroupsView'

    def get_js_view_data(self):
        # Fetch the list of IDs of groups the user has joined.
        joined_group_ids = self.user.review_groups.values_list('pk', flat=True)

        # Fetch the list of gorups available to the user.
        serialized_groups = SortedDict()
        serialized_groups[''] = self._serialize_groups(None, joined_group_ids)

        for local_site in self.user.local_site.order_by('name'):
            serialized_groups[local_site.name] = self._serialize_groups(
                local_site, joined_group_ids)

        return {
            'groups': serialized_groups,
        }

    def _serialize_groups(self, local_site, joined_group_ids):
        if local_site:
            local_site_name = local_site.name
        else:
            local_site_name = None

        groups = Group.objects.accessible(user=self.user,
                                          local_site=local_site)
        return [
            {
                'name': group.name,
                'reviewGroupID': group.pk,
                'displayName': group.display_name,
                'localSiteName': local_site_name,
                'joined': group.pk in joined_group_ids,
                'url': local_site_reverse('group',
                                          local_site_name=local_site_name,
                                          kwargs={'name': group.name}),
            }
            for group in groups.order_by('name')
        ]

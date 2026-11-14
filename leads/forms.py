from django import forms

from .models import Lead


class LeadUpdateForm(forms.ModelForm):
    note = forms.CharField(widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Add an internal note…"}), required=False)

    class Meta:
        model = Lead
        fields = ["status", "follow_up_at"]
        widgets = {"follow_up_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["follow_up_at"].input_formats = ["%Y-%m-%dT%H:%M"]


class LeadFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search", widget=forms.TextInput(attrs={"placeholder": "Title, snippet, location or contact"}))
    status = forms.ChoiceField(required=False, choices=[("", "All statuses"), *Lead.Status.choices])
    label = forms.ChoiceField(required=False, choices=[("", "All labels"), *Lead.Label.choices])
    claimed = forms.ChoiceField(required=False, choices=[("", "All leads"), ("mine", "Claimed by me"), ("unclaimed", "Unclaimed")])
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    posted = forms.ChoiceField(required=False, label="Posted")

    def __init__(self, *args, recent_days=7, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["posted"].choices = [("recent", f"Last {recent_days} days"), ("all", "Any time")]


class AILeadSearchForm(forms.Form):
    service = forms.CharField(
        label="Service",
        max_length=80,
        widget=forms.TextInput(attrs={"list": "service-suggestions", "placeholder": "Type a service, e.g. respite care", "autocomplete": "off"}),
    )
    location = forms.CharField(label="Location", max_length=80, widget=forms.TextInput(attrs={"placeholder": "Melbourne, VIC"}))


# ---- Settings screens (replace the Django admin for day-to-day use) ----

import json

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password

from .models import Keyword, Membership, SearchProfile, ServiceCategory, Source


class SearchProfileForm(forms.ModelForm):
    class Meta:
        model = SearchProfile
        fields = ["states", "locations", "radius_km", "freshness_days", "daily_lead_limit"]
        labels = {
            "states": "States you serve",
            "locations": "Suburbs or postcodes",
            "radius_km": "Distance around them (km)",
            "freshness_days": "Ignore posts older than (days)",
            "daily_lead_limit": "Best leads shown on the dashboard",
        }
        help_texts = {"states": "For example: VIC, NSW", "locations": "One per line. Leave empty to search the whole state."}
        widgets = {"locations": forms.Textarea(attrs={"rows": 4, "placeholder": "Melbourne\nGeelong\n3000"})}

    def clean_states(self):
        states = [x.strip().upper() for x in self.cleaned_data["states"].split(",") if x.strip()]
        valid = {"ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"}
        unknown = [x for x in states if x not in valid]
        if unknown:
            raise forms.ValidationError(f"Unknown state: {', '.join(unknown)}. Use codes such as VIC or NSW.")
        return ", ".join(states)


class KeywordForm(forms.ModelForm):
    class Meta:
        model = Keyword
        fields = ["term", "kind", "category"]
        labels = {"term": "Word or phrase", "kind": "Type", "category": "Service"}
        widgets = {"term": forms.TextInput(attrs={"placeholder": "e.g. looking for a support worker"})}

    def __init__(self, *args, profile=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.profile = profile
        self.fields["kind"].choices = [(Keyword.Kind.POSITIVE, "Find posts with this"), (Keyword.Kind.NEGATIVE, "Skip posts with this")]
        self.fields["category"].queryset = ServiceCategory.objects.filter(active=True).order_by("name")
        self.fields["category"].empty_label = "Any service"
        self.fields["category"].required = False

    def clean(self):
        data = super().clean()
        term = (data.get("term") or "").strip()
        if term and self.profile and Keyword.objects.filter(profile=self.profile, term__iexact=term, kind=data.get("kind")).exists():
            raise forms.ValidationError(f'"{term}" is already in the list.')
        return data


class ServiceCategoryForm(forms.ModelForm):
    class Meta:
        model = ServiceCategory
        fields = ["name", "synonyms", "active"]
        labels = {"synonyms": "Other words people use", "active": "In use"}
        help_texts = {"synonyms": "One per line, for example: in-home support"}
        widgets = {"synonyms": forms.Textarea(attrs={"rows": 3})}


class SourceForm(forms.ModelForm):
    selectors = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 5, "spellcheck": "false", "class": "code"}),
        help_text='Website sources only. JSON, for example {"item": ".post", "title": "h2", "link": "a"}',
    )

    class Meta:
        model = Source
        fields = [
            "name", "source_type", "base_url", "search_url_template", "selectors",
            "robots_reviewed", "terms_reviewed", "review_status", "enabled",
            "request_delay_seconds", "max_pages",
        ]
        labels = {
            "source_type": "Type",
            "base_url": "Website address",
            "search_url_template": "Search address",
            "robots_reviewed": "I checked robots.txt allows this",
            "terms_reviewed": "I checked the site's terms allow this",
            "review_status": "Decision",
            "enabled": "Include in scans",
            "request_delay_seconds": "Seconds between requests",
            "max_pages": "Pages per search",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial["selectors"] = json.dumps(self.instance.selectors or {}, indent=2)

    def clean_selectors(self):
        raw = (self.cleaned_data.get("selectors") or "").strip()
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"This is not valid JSON: {exc.msg} (line {exc.lineno}).") from exc
        if not isinstance(value, dict):
            raise forms.ValidationError("Use an object, for example {\"item\": \".post\"}.")
        return value

    def clean(self):
        data = super().clean()
        if data.get("enabled") and not (
            data.get("review_status") == Source.ReviewStatus.APPROVED and data.get("robots_reviewed") and data.get("terms_reviewed")
        ):
            self.add_error("enabled", "Approve the source and tick both checks before including it in scans.")
        return data


class TeamMemberForm(forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False, help_text="Used for password resets and the daily summary.")
    username = forms.CharField(max_length=150, help_text="What they type to sign in.")
    role = forms.ChoiceField(choices=Membership.Role.choices, initial=Membership.Role.EMPLOYEE)
    password = forms.CharField(widget=forms.PasswordInput(render_value=False), required=False)
    active = forms.BooleanField(required=False, initial=True, label="Can sign in")

    def __init__(self, *args, user=None, membership=None, can_grant_owner=False, **kwargs):
        self.user = user
        self.membership = membership
        super().__init__(*args, **kwargs)
        if not can_grant_owner:
            self.fields["role"].choices = [c for c in Membership.Role.choices if c[0] != Membership.Role.OWNER]
        if user:
            self.fields["password"].label = "New password"
            self.fields["password"].help_text = "Leave empty to keep the current password."
        else:
            self.fields["password"].required = True

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        others = User.objects.filter(username__iexact=username)
        if self.user:
            others = others.exclude(pk=self.user.pk)
        if others.exists():
            raise forms.ValidationError("That username is taken.")
        return username

    def clean_password(self):
        password = self.cleaned_data.get("password")
        if password:
            validate_password(password, self.user)
        return password

from django import forms
from django.contrib import admin
from django.db.models import Count
from django.utils import timezone
from unfold.admin import ModelAdmin, TabularInline

from .models import (
    AIConfiguration,
    AuditLog,
    Keyword,
    Lead,
    LeadActivity,
    LoginThrottle,
    Membership,
    Notification,
    Organisation,
    ScrapeRun,
    SearchProfile,
    ServiceCategory,
    Source,
)


def dashboard_callback(request, context):
    context.update(
        {
            "lead_count": Lead.objects.count(),
            "new_leads": Lead.objects.filter(status=Lead.Status.NEW).count(),
            "converted_leads": Lead.objects.filter(status=Lead.Status.CONVERTED).count(),
            "source_errors": Source.objects.exclude(last_error="").count(),
        }
    )
    return context


class MembershipInline(TabularInline):
    model = Membership
    extra = 0


@admin.register(Organisation)
class OrganisationAdmin(ModelAdmin):
    list_display = ("name", "slug", "active", "created_at")
    inlines = [MembershipInline]


@admin.register(Membership)
class MembershipAdmin(ModelAdmin):
    list_display = ("user", "organisation", "role", "active")
    list_filter = ("role", "active", "organisation")
    search_fields = ("user__username", "user__email", "organisation__name")


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(ModelAdmin):
    list_display = ("name", "slug", "active", "updated_at")
    list_filter = ("active",)
    search_fields = ("name", "synonyms")
    prepopulated_fields = {"slug": ("name",)}


class KeywordInline(TabularInline):
    model = Keyword
    extra = 1


@admin.register(SearchProfile)
class SearchProfileAdmin(ModelAdmin):
    list_display = ("name", "organisation", "states", "radius_km", "daily_lead_limit", "scan_interval_hours", "active")
    list_filter = ("active", "organisation")
    inlines = [KeywordInline]


@admin.register(Keyword)
class KeywordAdmin(ModelAdmin):
    list_display = ("term", "profile", "kind", "category", "generated", "enabled")
    list_filter = ("kind", "generated", "enabled", "category")
    search_fields = ("term",)
    actions = ("generate_controlled_variants",)

    @admin.action(description="Generate disabled variants for review")
    def generate_controlled_variants(self, request, queryset):
        created = 0
        for keyword in queryset.filter(kind=Keyword.Kind.POSITIVE, category__isnull=False).select_related("category", "profile"):
            terms = set(keyword.category.terms())
            terms.add(keyword.term.replace("-", " "))
            terms.add(keyword.term.replace(" ", "-"))
            terms.add(keyword.term.replace("home care", "homecare"))
            for term in {x.strip() for x in terms if x.strip()}:
                if term.lower() == keyword.term.lower():
                    continue
                _, was_created = Keyword.objects.get_or_create(
                    profile=keyword.profile,
                    term=term,
                    kind=Keyword.Kind.POSITIVE,
                    defaults={"category": keyword.category, "generated": True, "enabled": False},
                )
                created += int(was_created)
        self.message_user(request, f"Created {created} disabled keyword variant(s) for review.")


@admin.register(Source)
class SourceAdmin(ModelAdmin):
    list_display = ("name", "source_type", "review_status", "robots_reviewed", "terms_reviewed", "enabled", "last_checked_at")
    list_filter = ("source_type", "review_status", "enabled", "robots_reviewed", "terms_reviewed")
    search_fields = ("name", "base_url", "search_url_template")
    readonly_fields = ("last_checked_at", "last_error", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "source_type", "base_url", "search_url_template", "selectors")}),
        ("Permission review", {"fields": ("review_status", "robots_reviewed", "terms_reviewed", "enabled")}),
        ("Crawl controls", {"fields": ("request_delay_seconds", "max_pages")}),
        ("Health", {"fields": ("last_checked_at", "last_error", "created_at", "updated_at")}),
    )


@admin.register(ScrapeRun)
class ScrapeRunAdmin(ModelAdmin):
    list_display = ("source", "profile", "status", "started_at", "records_seen", "leads_created", "duplicates_found")
    list_filter = ("status", "source", "profile")
    readonly_fields = tuple(field.name for field in ScrapeRun._meta.fields)

    def has_add_permission(self, request):
        return False


class LeadActivityInline(TabularInline):
    model = LeadActivity
    extra = 0
    readonly_fields = ("user", "action", "note", "from_status", "to_status", "follow_up_at", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Lead)
class LeadAdmin(ModelAdmin):
    list_display = ("short_title", "service_category", "location", "label", "relevance_score", "status", "claimed_by", "source", "published_at")
    list_filter = ("status", "label", "lead_type", "state", "service_category", "source")
    search_fields = ("title", "snippet", "public_name", "public_phone", "public_email", "suburb", "postcode")
    readonly_fields = ("content_hash", "discovered_at", "last_checked_at", "created_at", "updated_at")
    inlines = [LeadActivityInline]

    @admin.display(description="Title")
    def short_title(self, obj):
        return obj.title[:75]

    @admin.display(description="Location")
    def location(self, obj):
        return " ".join(x for x in (obj.suburb, obj.state, obj.postcode) if x) or "Unknown"


@admin.register(LeadActivity)
class LeadActivityAdmin(ModelAdmin):
    list_display = ("lead", "user", "action", "from_status", "to_status", "created_at")
    list_filter = ("action", "to_status")
    search_fields = ("lead__title", "note", "user__username")


@admin.register(Notification)
class NotificationAdmin(ModelAdmin):
    list_display = ("user", "title", "created_at", "read_at")
    list_filter = ("read_at",)
    search_fields = ("user__username", "title", "message")


@admin.register(AuditLog)
class AuditLogAdmin(ModelAdmin):
    list_display = ("created_at", "user", "action", "object_type", "object_id")
    list_filter = ("action", "object_type")
    search_fields = ("user__username", "object_id")
    readonly_fields = tuple(field.name for field in AuditLog._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class AIConfigurationForm(forms.ModelForm):
    api_key = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False), help_text="Leave blank to keep the existing encrypted key.")

    class Meta:
        model = AIConfiguration
        fields = ("organisation", "enabled", "base_url", "model", "api_key")

    def save(self, commit=True):
        instance = super().save(commit=False)
        raw_key = self.cleaned_data.get("api_key")
        if raw_key:
            instance.set_api_key(raw_key)
        if commit:
            instance.save()
        return instance


@admin.register(AIConfiguration)
class AIConfigurationAdmin(ModelAdmin):
    form = AIConfigurationForm
    list_display = ("organisation", "enabled", "base_url", "model", "updated_at")


@admin.register(LoginThrottle)
class LoginThrottleAdmin(ModelAdmin):
    list_display = ("identifier_hash", "failures", "last_failed_at", "locked_until")
    readonly_fields = ("identifier_hash", "failures", "first_failed_at", "last_failed_at", "locked_until")

    def has_add_permission(self, request):
        return False

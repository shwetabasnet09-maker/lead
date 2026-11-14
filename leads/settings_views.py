"""Settings screens for owners and managers, so nobody needs the Django admin day to day."""
from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import KeywordForm, SearchProfileForm, ServiceCategoryForm, SourceForm, TeamMemberForm
from .jobs import celery_enabled, start_scan
from .models import AuditLog, Keyword, Membership, ScrapeRun, SearchProfile, ServiceCategory, Source
from .services import ScanAlreadyRunning, scan_is_running
from .views import _membership, _organisation


def can_manage(user) -> bool:
    if user.is_superuser:
        return True
    membership = _membership(user)
    return bool(membership and membership.role in {Membership.Role.OWNER, Membership.Role.MANAGER})


def _is_owner(user) -> bool:
    membership = _membership(user)
    return user.is_superuser or bool(membership and membership.role == Membership.Role.OWNER)


def manager_required(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not can_manage(request.user):
            raise PermissionDenied("Only owners and managers can change settings.")
        return view(request, *args, **kwargs)

    return wrapper


def _audit(request, action, obj, **metadata):
    AuditLog.objects.create(user=request.user, action=action, object_type=type(obj).__name__, object_id=str(obj.pk), metadata=metadata)


def _profile(organisation) -> SearchProfile:
    profile = SearchProfile.objects.filter(organisation=organisation, active=True).order_by("id").first()
    return profile or SearchProfile.objects.create(organisation=organisation)


def source_state(source: Source) -> tuple[str, str]:
    """A plain-English status and a CSS class for a source."""
    if source.review_status == Source.ReviewStatus.REJECTED:
        return "Rejected", "status-failed"
    if not (source.review_status == Source.ReviewStatus.APPROVED and source.robots_reviewed and source.terms_reviewed):
        return "Needs review", "status-partial"
    if not source.enabled:
        return "Off", ""
    if source.last_error:
        return "Problem", "status-failed"
    return "On", "status-success"


# ---- Scans and sources ----

@manager_required
def sources(request):
    items = list(Source.objects.order_by("-enabled", "name"))
    for source in items:
        source.state_label, source.state_class = source_state(source)
    context = {
        "sources": items,
        "runs": ScrapeRun.objects.select_related("source").order_by("-started_at")[:15],
        "running": scan_is_running(),
        "celery": celery_enabled(),
        "runnable_count": sum(1 for s in items if s.is_runnable),
    }
    return render(request, "leads/settings/sources.html", context)


@manager_required
def source_edit(request, pk=None):
    source = get_object_or_404(Source, pk=pk) if pk else None
    form = SourceForm(request.POST or None, instance=source)
    if request.method == "POST" and form.is_valid():
        source = form.save()
        _audit(request, "source_saved", source, enabled=source.enabled, review_status=source.review_status)
        messages.success(request, f"{source.name} saved.")
        return redirect("settings-sources")
    return render(request, "leads/settings/source_form.html", {"form": form, "source": source})


@manager_required
@require_POST
def source_toggle(request, pk):
    source = get_object_or_404(Source, pk=pk)
    if not source.enabled and not (source.review_status == Source.ReviewStatus.APPROVED and source.robots_reviewed and source.terms_reviewed):
        messages.error(request, f"Review {source.name} before turning it on.")
        return redirect("settings-source-edit", pk=source.pk)
    source.enabled = not source.enabled
    source.save(update_fields=["enabled", "updated_at"])
    _audit(request, "source_toggled", source, enabled=source.enabled)
    messages.success(request, f"{source.name} is now {'on' if source.enabled else 'off'}.")
    return redirect("settings-sources")


@manager_required
@require_POST
def scan_now(request):
    source = None
    if request.POST.get("source"):
        source = get_object_or_404(Source, pk=request.POST["source"])
        if not source.is_runnable:
            messages.error(request, f"{source.name} is not on. Review it and turn it on first.")
            return redirect("settings-sources")
    try:
        start_scan(source.pk if source else None)
    except ScanAlreadyRunning as exc:
        messages.error(request, str(exc))
    else:
        AuditLog.objects.create(user=request.user, action="scan_started", object_type="Source", object_id=str(source.pk) if source else "")
        messages.success(request, f"Scanning {source.name if source else 'all sources'}. New leads appear as they are found.")
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(next_url)
    return redirect("settings-sources")


@login_required
def scan_status(request):
    latest = ScrapeRun.objects.order_by("-started_at").values_list("pk", flat=True).first()
    return JsonResponse({"running": scan_is_running(), "latest_run": latest})


# ---- Search area and keywords ----

@manager_required
def search(request):
    profile = _profile(_organisation(request.user))
    profile_form = SearchProfileForm(instance=profile)
    keyword_form = KeywordForm(profile=profile)
    if request.method == "POST":
        if request.POST.get("form") == "profile":
            profile_form = SearchProfileForm(request.POST, instance=profile)
            if profile_form.is_valid():
                profile_form.save()
                _audit(request, "search_profile_saved", profile)
                messages.success(request, "Search area saved. It applies from the next scan.")
                return redirect("settings-search")
        else:
            keyword_form = KeywordForm(request.POST, profile=profile)
            if keyword_form.is_valid():
                keyword = keyword_form.save(commit=False)
                keyword.profile = profile
                keyword.term = keyword.term.strip()
                keyword.save()
                _audit(request, "keyword_added", keyword, term=keyword.term)
                messages.success(request, f'Added "{keyword.term}".')
                return redirect("settings-search")
    keywords = profile.keywords.select_related("category")
    return render(
        request,
        "leads/settings/search.html",
        {
            "profile_form": profile_form,
            "keyword_form": keyword_form,
            "positive": keywords.filter(kind=Keyword.Kind.POSITIVE),
            "negative": keywords.filter(kind=Keyword.Kind.NEGATIVE),
        },
    )


def _keyword(request, pk) -> Keyword:
    return get_object_or_404(Keyword, pk=pk, profile__organisation=_organisation(request.user))


@manager_required
@require_POST
def keyword_toggle(request, pk):
    keyword = _keyword(request, pk)
    keyword.enabled = not keyword.enabled
    keyword.save(update_fields=["enabled", "updated_at"])
    _audit(request, "keyword_toggled", keyword, enabled=keyword.enabled)
    return redirect("settings-search")


@manager_required
@require_POST
def keyword_delete(request, pk):
    keyword = _keyword(request, pk)
    _audit(request, "keyword_deleted", keyword, term=keyword.term)
    keyword.delete()
    messages.success(request, f'Removed "{keyword.term}".')
    return redirect("settings-search")


# ---- Services ----

@manager_required
def services(request, pk=None):
    editing = get_object_or_404(ServiceCategory, pk=pk) if pk else None
    form = ServiceCategoryForm(request.POST or None, instance=editing, initial=None if editing else {"active": True})
    if request.method == "POST" and form.is_valid():
        category = form.save(commit=False)
        if not category.slug:
            from django.utils.text import slugify

            base = slugify(category.name)[:45] or "service"
            slug, n = base, 2
            while ServiceCategory.objects.filter(slug=slug).exclude(pk=category.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            category.slug = slug
        category.save()
        _audit(request, "service_saved", category, name=category.name)
        messages.success(request, f"{category.name} saved.")
        return redirect("settings-services")
    return render(
        request,
        "leads/settings/services.html",
        {"form": form, "editing": editing, "categories": ServiceCategory.objects.order_by("-active", "name")},
    )


# ---- Team ----

@manager_required
def team(request):
    organisation = _organisation(request.user)
    members = organisation.memberships.select_related("user").order_by("-active", "role", "user__first_name", "user__username")
    return render(request, "leads/settings/team.html", {"members": members, "is_owner": _is_owner(request.user)})


@manager_required
def team_member(request, pk=None):
    organisation = _organisation(request.user)
    membership = get_object_or_404(Membership.objects.select_related("user"), pk=pk, organisation=organisation) if pk else None
    user = membership.user if membership else None
    is_owner = _is_owner(request.user)
    if membership and membership.role == Membership.Role.OWNER and not is_owner:
        raise PermissionDenied("Only an owner can change another owner.")
    initial = {}
    if membership:
        initial = {"first_name": user.first_name, "last_name": user.last_name, "email": user.email, "username": user.username, "role": membership.role, "active": membership.active and user.is_active}
    form = TeamMemberForm(request.POST or None, initial=initial or None, user=user, membership=membership, can_grant_owner=is_owner)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        if user == request.user and (not data["active"] or data["role"] not in {Membership.Role.OWNER, Membership.Role.MANAGER}) and not request.user.is_superuser:
            form.add_error(None, "You cannot remove your own access to settings. Ask another owner to do it.")
        else:
            with transaction.atomic():
                user = user or User(username=data["username"])
                user.username = data["username"]
                user.first_name = data["first_name"]
                user.last_name = data["last_name"]
                user.email = data["email"]
                user.is_active = data["active"]
                if data["password"]:
                    user.set_password(data["password"])
                user.save()
                membership = membership or Membership(organisation=organisation, user=user)
                membership.role = data["role"]
                membership.active = data["active"]
                membership.save()
            _audit(request, "team_member_saved", membership, role=membership.role, active=membership.active)
            messages.success(request, f"{user.get_full_name() or user.username} saved.")
            return redirect("settings-team")
    return render(request, "leads/settings/team_member.html", {"form": form, "membership": membership})

from __future__ import annotations

import csv
from copy import copy
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import HttpResponse, HttpResponseForbidden
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import gemini, groq
from .forms import AILeadSearchForm, LeadFilterForm, LeadUpdateForm
from .models import AuditLog, Lead, LeadActivity, Membership, Notification, ScrapeRun, ServiceCategory, Source
from .services import active_profile, default_organisation, freshness_days_for, recent_leads_q, scan_is_running


def _membership(user):
    return Membership.objects.filter(user=user, active=True).select_related("organisation").first()


def _can_edit(user) -> bool:
    if user.is_superuser:
        return True
    membership = _membership(user)
    return bool(membership and membership.role != Membership.Role.READ_ONLY)


def _organisation(user):
    membership = _membership(user)
    if membership:
        return membership.organisation
    if user.is_superuser:
        return default_organisation()
    raise PermissionDenied("Your account is not assigned to an active organisation.")


@login_required
def dashboard(request):
    organisation = _organisation(request.user)
    leads = Lead.objects.filter(organisation=organisation)
    today = timezone.localdate()
    profile = active_profile(organisation)
    daily_limit = profile.daily_lead_limit if profile else 6
    recent_days = freshness_days_for(organisation)
    recent_likely = leads.filter(recent_leads_q(recent_days), label=Lead.Label.LIKELY, published_at__isnull=False)
    curated = recent_likely.exclude(
        status__in=[Lead.Status.INVALID, Lead.Status.ARCHIVED, Lead.Status.CLOSED]
    ).order_by(F("relevance_score").desc(nulls_last=True), "-published_at")[:daily_limit]
    context = {
        "recent_days": recent_days,
        "new_today": leads.filter(discovered_at__date=today).count(),
        "likely": recent_likely.filter(status=Lead.Status.NEW).count(),
        "follow_ups": leads.filter(follow_up_at__date__lte=today).exclude(status__in=[Lead.Status.CONVERTED, Lead.Status.CLOSED]).count(),
        "converted_month": leads.filter(status=Lead.Status.CONVERTED, updated_at__date__gte=today.replace(day=1)).count(),
        "recent_leads": curated.select_related("source", "service_category", "claimed_by"),
        "recent_runs": ScrapeRun.objects.select_related("source").order_by("-started_at")[:5],
        "source_errors": Source.objects.exclude(last_error="").filter(enabled=True)[:5],
        "scan_running": scan_is_running(),
        "last_scan": ScrapeRun.objects.filter(finished_at__isnull=False).order_by("-finished_at").first(),
        "scan_stale_before": timezone.now() - timedelta(hours=12),
        "newest_lead": leads.exclude(published_at__isnull=True).order_by("-published_at").values_list("published_at", flat=True).first(),
        "status_counts": list(leads.values("status").annotate(total=Count("id")).order_by("-total")),
    }
    return render(request, "leads/dashboard.html", context)


def _filtered_leads(request):
    organisation = _organisation(request.user)
    queryset = Lead.objects.filter(organisation=organisation).select_related("source", "service_category", "claimed_by")
    recent_days = freshness_days_for(organisation)
    form = LeadFilterForm(request.GET, recent_days=recent_days)
    if form.is_valid():
        data = form.cleaned_data
        if data["posted"] != "all":
            # Claimed leads stay visible so that work in progress never disappears.
            queryset = queryset.filter(recent_leads_q(recent_days) | Q(claimed_by__isnull=False))
        if data["q"]:
            q = data["q"]
            queryset = queryset.filter(
                Q(title__icontains=q)
                | Q(snippet__icontains=q)
                | Q(suburb__icontains=q)
                | Q(postcode__icontains=q)
                | Q(public_name__icontains=q)
                | Q(public_phone__icontains=q)
                | Q(public_email__icontains=q)
            )
        if data["status"]:
            queryset = queryset.filter(status=data["status"])
        else:
            # Posts the AI rejected are kept for audit, but only shown when asked for.
            queryset = queryset.exclude(status=Lead.Status.DISQUALIFIED)
        for field in ("priority", "intent", "coverage", "source"):
            if data[field]:
                queryset = queryset.filter(**{field: data[field]})
        if data["label"]:
            queryset = queryset.filter(label=data["label"])
        if data["claimed"] == "mine":
            queryset = queryset.filter(claimed_by=request.user)
        elif data["claimed"] == "unclaimed":
            queryset = queryset.filter(claimed_by__isnull=True)
        if data["date_from"]:
            queryset = queryset.filter(discovered_at__date__gte=data["date_from"])
        if data["date_to"]:
            queryset = queryset.filter(discovered_at__date__lte=data["date_to"])
    return queryset, form


@login_required
def lead_list(request):
    queryset, form = _filtered_leads(request)
    paginator = Paginator(queryset, 30)
    page_obj = paginator.get_page(request.GET.get("page"))
    context = {"page_obj": page_obj, "filter_form": form, "recent_days": freshness_days_for(_organisation(request.user))}
    if not paginator.count and form.is_valid() and form.cleaned_data["posted"] != "all":
        # Explain an empty list instead of looking broken: say how many older leads the date window hides.
        params = request.GET.copy()
        params["posted"] = "all"
        params.pop("page", None)
        older, _ = _filtered_leads(_with_params(request, params))
        context.update(older_count=older.count(), show_all_query=params.urlencode())
    return render(request, "leads/lead_list.html", context)


def _with_params(request, params):
    clone = copy(request)
    clone.GET = params
    return clone


@login_required
def lead_detail(request, pk):
    lead = get_object_or_404(
        Lead.objects.select_related("source", "service_category", "claimed_by", "duplicate_of"),
        pk=pk,
        organisation=_organisation(request.user),
    )
    form = LeadUpdateForm(instance=lead)
    return render(request, "leads/lead_detail.html", {"lead": lead, "form": form, "can_edit": _can_edit(request.user)})


@login_required
@require_POST
def claim_lead(request, pk):
    if not _can_edit(request.user):
        return HttpResponseForbidden("Read-only accounts cannot claim leads")
    with transaction.atomic():
        lead = get_object_or_404(Lead.objects.select_for_update(), pk=pk, organisation=_organisation(request.user))
        if lead.claimed_by and lead.claimed_by != request.user:
            messages.error(request, f"This lead is already claimed by {lead.claimed_by.get_full_name() or lead.claimed_by.username}.")
        else:
            lead.claimed_by = request.user
            lead.claimed_at = timezone.now()
            lead.save(update_fields=["claimed_by", "claimed_at", "updated_at"])
            LeadActivity.objects.create(lead=lead, user=request.user, action="Lead claimed")
            AuditLog.objects.create(user=request.user, action="claim", object_type="Lead", object_id=str(lead.pk))
            messages.success(request, "Lead claimed.")
    return redirect("lead-detail", pk=pk)


@login_required
@require_POST
def release_lead(request, pk):
    if not _can_edit(request.user):
        return HttpResponseForbidden("Read-only accounts cannot release leads")
    lead = get_object_or_404(Lead, pk=pk, organisation=_organisation(request.user))
    membership = _membership(request.user)
    can_override = request.user.is_superuser or (membership and membership.role in {Membership.Role.OWNER, Membership.Role.MANAGER})
    if lead.claimed_by != request.user and not can_override:
        return HttpResponseForbidden("Only the claimant or a manager can release this lead")
    lead.claimed_by = None
    lead.claimed_at = None
    lead.save(update_fields=["claimed_by", "claimed_at", "updated_at"])
    LeadActivity.objects.create(lead=lead, user=request.user, action="Lead released")
    messages.success(request, "Lead released.")
    return redirect("lead-detail", pk=pk)


@login_required
@require_POST
def update_lead(request, pk):
    if not _can_edit(request.user):
        return HttpResponseForbidden("Read-only accounts cannot update leads")
    lead = get_object_or_404(Lead, pk=pk, organisation=_organisation(request.user))
    if lead.claimed_by and lead.claimed_by != request.user:
        membership = _membership(request.user)
        if not request.user.is_superuser and (not membership or membership.role not in {Membership.Role.OWNER, Membership.Role.MANAGER}):
            return HttpResponseForbidden("This lead is claimed by another employee")
    form = LeadUpdateForm(request.POST, instance=lead)
    if form.is_valid():
        previous = lead.status
        lead = form.save()
        LeadActivity.objects.create(
            lead=lead,
            user=request.user,
            action="Lead updated",
            note=form.cleaned_data["note"],
            from_status=previous,
            to_status=lead.status,
            follow_up_at=lead.follow_up_at,
        )
        AuditLog.objects.create(
            user=request.user,
            action="update",
            object_type="Lead",
            object_id=str(lead.pk),
            metadata={"from_status": previous, "to_status": lead.status},
        )
        messages.success(request, "Lead updated.")
    else:
        messages.error(request, "Please correct the form errors.")
    return redirect("lead-detail", pk=pk)


@login_required
def reports(request):
    leads = Lead.objects.filter(organisation=_organisation(request.user))
    since = timezone.now() - timedelta(days=30)
    by_status = list(leads.filter(discovered_at__gte=since).values("status").annotate(total=Count("id")).order_by("-total"))
    by_source = list(leads.filter(discovered_at__gte=since).values("source__name").annotate(total=Count("id")).order_by("-total")[:10])
    by_category = list(leads.filter(discovered_at__gte=since).values("service_category__name").annotate(total=Count("id")).order_by("-total")[:10])
    total = leads.filter(discovered_at__gte=since).count()
    converted = leads.filter(discovered_at__gte=since, status=Lead.Status.CONVERTED).count()
    conversion_rate = round((converted / total * 100), 1) if total else 0
    return render(
        request,
        "leads/reports.html",
        {"by_status": by_status, "by_source": by_source, "by_category": by_category, "total": total, "converted": converted, "conversion_rate": conversion_rate},
    )


@login_required
def export_leads(request):
    queryset, _ = _filtered_leads(request)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="ndis-leads.csv"'
    writer = csv.writer(response)
    writer.writerow(["Title", "Service", "Location", "Status", "Label", "Name", "Phone", "Email", "Source", "URL", "Published", "Discovered"])
    for lead in queryset.iterator():
        writer.writerow(
            [
                lead.title,
                lead.service_category.name if lead.service_category else "",
                " ".join(x for x in [lead.suburb, lead.state, lead.postcode] if x),
                lead.get_status_display(),
                lead.get_label_display(),
                lead.public_name,
                lead.public_phone,
                lead.public_email,
                lead.source.name,
                lead.source_url,
                lead.published_at.isoformat() if lead.published_at else "",
                lead.discovered_at.isoformat(),
            ]
        )
    AuditLog.objects.create(user=request.user, action="export", object_type="Lead", metadata={"count": queryset.count()})
    return response


@login_required
@never_cache
def ai_search(request):
    """Live AI search. Results go only to the person who asked and are never saved (see leads/gemini.py)."""
    organisation = _organisation(request.user)
    days = freshness_days_for(organisation)
    services = list(ServiceCategory.objects.filter(active=True).order_by("name").values_list("name", flat=True))
    provider = next((candidate for candidate in (groq, gemini) if candidate.is_configured()), None)
    context = {"days": days, "configured": bool(provider), "provider_name": provider.PROVIDER_NAME if provider else "AI", "can_edit": _can_edit(request.user), "services": services}
    if request.method == "POST":
        if not context["can_edit"]:
            return HttpResponseForbidden("Read-only accounts cannot run AI searches")
        form = AILeadSearchForm(request.POST)
        if form.is_valid() and context["configured"]:
            try:
                context["result"] = provider.search_for_leads(form.cleaned_data["service"], form.cleaned_data["location"], days)
                AuditLog.objects.create(user=request.user, action="ai_search", object_type="Lead")
            except (gemini.GeminiError, groq.GroqError) as exc:
                messages.error(request, str(exc))
    else:
        form = AILeadSearchForm()
    context["form"] = form
    return render(request, "leads/ai_search.html", context)


@login_required
def notifications(request):
    items = Notification.objects.filter(user=request.user)
    items.filter(read_at__isnull=True).update(read_at=timezone.now())
    return render(request, "leads/notifications.html", {"notifications": items[:100]})

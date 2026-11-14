from django.urls import path

from . import settings_views, views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("leads/", views.lead_list, name="lead-list"),
    path("leads/export/", views.export_leads, name="lead-export"),
    path("leads/<int:pk>/", views.lead_detail, name="lead-detail"),
    path("leads/<int:pk>/claim/", views.claim_lead, name="lead-claim"),
    path("leads/<int:pk>/release/", views.release_lead, name="lead-release"),
    path("leads/<int:pk>/update/", views.update_lead, name="lead-update"),
    path("ai-search/", views.ai_search, name="ai-search"),
    path("reports/", views.reports, name="reports"),
    path("notifications/", views.notifications, name="notifications"),
    path("settings/", settings_views.sources, name="settings-sources"),
    path("settings/sources/new/", settings_views.source_edit, name="settings-source-new"),
    path("settings/sources/<int:pk>/", settings_views.source_edit, name="settings-source-edit"),
    path("settings/sources/<int:pk>/toggle/", settings_views.source_toggle, name="settings-source-toggle"),
    path("settings/scan/", settings_views.scan_now, name="settings-scan"),
    path("settings/scan/status/", settings_views.scan_status, name="settings-scan-status"),
    path("settings/search/", settings_views.search, name="settings-search"),
    path("settings/keywords/<int:pk>/toggle/", settings_views.keyword_toggle, name="settings-keyword-toggle"),
    path("settings/keywords/<int:pk>/delete/", settings_views.keyword_delete, name="settings-keyword-delete"),
    path("settings/services/", settings_views.services, name="settings-services"),
    path("settings/services/<int:pk>/", settings_views.services, name="settings-service-edit"),
    path("settings/team/", settings_views.team, name="settings-team"),
    path("settings/team/new/", settings_views.team_member, name="settings-team-new"),
    path("settings/team/<int:pk>/", settings_views.team_member, name="settings-team-member"),
]

"""The Django admin, for site admins (is_staff). It is one more write path, so
it gets the same deadline check as the pages and the API, as a form error;
the trigger still backs it up."""
from django import forms
from django.contrib import admin

from . import audit
from .access import submissions_closed_reason
from .models import (ApiToken, AuditLog, Event, JudgeAssignment, Membership, Prize, Project, RoleInvite,
                     RubricCriterion, Team, TeamMember, Track, User)


class AuditedAdmin(admin.ModelAdmin):
    """Admin writes land in the audit log like any other change."""

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit.record(f"admin.{'change' if change else 'add'}", request=request, event=getattr(obj, "event", None),
                     obj=obj, after={f: str(form.cleaned_data.get(f)) for f in form.changed_data})

    def delete_model(self, request, obj):
        audit.record("admin.delete", request=request, event=getattr(obj, "event", None), obj=obj, before={"repr": str(obj)})
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self.delete_model(request, obj)


@admin.register(User)
class UserAdmin(AuditedAdmin):
    list_display = ["email", "name", "is_staff", "is_active", "date_joined"]
    search_fields = ["email", "name"]
    exclude = ["password", "user_permissions", "groups"]


class TrackInline(admin.TabularInline):
    model = Track
    extra = 0


class PrizeInline(admin.TabularInline):
    model = Prize
    extra = 0


@admin.register(Event)
class EventAdmin(AuditedAdmin):
    list_display = ["name", "slug", "submissions_open", "submissions_close", "results_published_at"]
    inlines = [TrackInline, PrizeInline]


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = "__all__"

    def clean(self):
        data = super().clean()
        event = data.get("event") or getattr(self.instance, "event", None)
        changed = set(self.changed_data) - {"duplicate_of"}
        if event and changed and (reason := submissions_closed_reason(event)):
            raise forms.ValidationError(reason)
        return data


@admin.register(Project)
class ProjectAdmin(AuditedAdmin):
    form = ProjectForm
    list_display = ["title", "event", "team", "status", "submitted_at", "duplicate_of"]
    list_filter = ["event", "status"]
    search_fields = ["title", "team__name"]

    def has_delete_permission(self, request, obj=None):
        return obj is None or not submissions_closed_reason(obj.event)


@admin.register(Team)
class TeamAdmin(AuditedAdmin):
    list_display = ["name", "event", "external_id"]
    list_filter = ["event"]


@admin.register(Membership)
class MembershipAdmin(AuditedAdmin):
    list_display = ["user", "event", "role"]
    list_filter = ["event", "role"]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read only. The table refuses UPDATE and DELETE anyway."""
    list_display = ["seq", "ts", "actor", "event", "action", "object_type", "object_id"]
    list_filter = ["action", "event"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in (TeamMember, RubricCriterion, JudgeAssignment, RoleInvite, ApiToken):
    admin.site.register(model, AuditedAdmin)

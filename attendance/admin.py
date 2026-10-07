from django.contrib import admin
from .models import Student, AttendanceSession, AttendanceLog, Exam, ExamVerificationLog, Subject, Department, Section, Period

admin.site.enable_nav_sidebar = False

@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('name', 'roll_number', 'department', 'section', 'created_at', 'has_encoding')
    list_filter = ('department', 'section')
    search_fields = ('name', 'roll_number', 'parent_email', 'parent_phone')
    readonly_fields = ('encoding', 'processing_status')

    def has_delete_permission(self, request, obj=None):
        return False

    def has_encoding(self, obj):
        from django.utils.safestring import mark_safe
        import json
        
        has_enc = bool(obj.encoding)
        status_data = {"step": 5 if has_enc else 0, "text": "Profile active!" if has_enc else "Queued for processing...", "percent": 100 if has_enc else 5}
        
        if obj.processing_status:
            try:
                status_data = json.loads(obj.processing_status)
            except Exception:
                pass
                
        step = status_data.get('step', 0)
        text = status_data.get('text', '')
        percent = status_data.get('percent', 0)
        
        if step == 5 or has_enc:
            return mark_safe(f'<div class="admin-status-badge-container encoded" data-student-id="{obj.id}" data-has-encoding="true">'
                             f'<span class="badge-status encoded"><i class="fa-solid fa-circle-check" style="margin-right: 5px;"></i> Active</span>'
                             f'</div>')
        elif step == -1:
            return mark_safe(f'<div class="admin-status-badge-container failed" data-student-id="{obj.id}" data-has-encoding="failed">'
                             f'<span class="badge-status failed" style="background: rgba(255, 61, 0, 0.1); color: #ff3d00; border: 1px solid rgba(255, 61, 0, 0.2);"><i class="fa-solid fa-triangle-exclamation" style="margin-right: 5px;"></i> Failed</span>'
                             f'<div class="admin-progress-step-label" style="font-size: 0.65rem; color: #ff3d00; margin-top: 2px;">{text}</div>'
                             f'</div>')
        else:
            return mark_safe(f'<div class="admin-status-badge-container processing" data-student-id="{obj.id}" data-has-encoding="false">'
                             f'<span class="badge-status processing" style="background: rgba(255, 179, 0, 0.08); color: #ffb300; border: 1px solid rgba(255, 179, 0, 0.2);"><i class="fa-solid fa-spinner fa-spin" style="margin-right: 5px;"></i> Processing</span>'
                             f'<div class="admin-progress-bar-container" style="width: 100px; height: 4px; background: rgba(255,255,255,0.1); border-radius: 2px; margin-top: 4px; overflow: hidden;">'
                             f'<div class="admin-progress-bar-fill pulse" style="width: {percent}%; height: 100%; background: linear-gradient(90deg, #ffb300, #ff9100); transition: width 0.4s ease-out;"></div>'
                             f'</div>'
                             f'<div class="admin-progress-step-label" style="font-size: 0.65rem; color: #8e8d9a; margin-top: 2px;">{text}</div>'
                             f'</div>')
    has_encoding.short_description = "Face Status"

@admin.register(AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = ('subject', 'department', 'section', 'period', 'date', 'start_time')
    list_filter = ('date', 'department', 'section', 'period')
    search_fields = ('subject',)

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(AttendanceLog)
class AttendanceLogAdmin(admin.ModelAdmin):
    list_display = ('student', 'session', 'status', 'time_marked', 'confidence')
    list_filter = ('session__subject', 'status', 'date')
    search_fields = ('student__name', 'session__subject')

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ('subject', 'exam_code', 'department', 'section', 'date', 'start_time', 'is_completed')
    list_filter = ('date', 'department', 'section', 'is_completed')
    search_fields = ('subject', 'exam_code')

@admin.register(ExamVerificationLog)
class ExamVerificationLogAdmin(admin.ModelAdmin):
    list_display = ('student', 'exam', 'status', 'time_verified', 'confidence')
    list_filter = ('exam__subject', 'status')
    search_fields = ('student__name', 'exam__subject')


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('code', 'name')
    search_fields = ('code', 'name')


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


@admin.register(Period)
class PeriodAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

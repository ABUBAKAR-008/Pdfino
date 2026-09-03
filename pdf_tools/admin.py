from django.contrib import admin

from .models import ConversionJob, Profile


@admin.register(ConversionJob)
class ConversionJobAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'tool_name', 'status', 'original_filename',
        'result_size_bytes', 'user', 'created_at', 'expires_at',
    )
    list_filter = ('status', 'category', 'tool_slug')
    search_fields = ('tool_name', 'original_filename', 'result_filename', 'id')
    readonly_fields = [f.name for f in ConversionJob._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'created_at')

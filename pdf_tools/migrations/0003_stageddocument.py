import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('pdf_tools', '0002_rename_pdf_tools_c_tool_sl_1a2b3c_idx_pdf_tools_c_tool_sl_2a9b8b_idx_and_more')]

    operations = [
        migrations.CreateModel(
            name='StagedDocument',
            fields=[
                ('token', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('session_key', models.CharField(blank=True, max_length=40)),
                ('relpath', models.CharField(max_length=255)),
                ('original_filename', models.CharField(blank=True, max_length=255)),
                ('size_bytes', models.PositiveBigIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                           related_name='staged_documents', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
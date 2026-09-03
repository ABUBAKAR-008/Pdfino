import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ConversionJob',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('tool_slug', models.CharField(db_index=True, max_length=64)),
                ('tool_name', models.CharField(max_length=128)),
                ('category', models.CharField(blank=True, choices=[
                    ('conversion', 'PDF Conversion'), ('organization', 'PDF Organization'),
                    ('optimization', 'PDF Optimization'), ('security', 'PDF Security'),
                    ('editing', 'PDF Editing'),
                ], max_length=32)),
                ('status', models.CharField(choices=[
                    ('pending', 'Pending'), ('processing', 'Processing'),
                    ('success', 'Success'), ('failed', 'Failed'),
                ], default='pending', max_length=16)),
                ('error_message', models.CharField(blank=True, max_length=500)),
                ('original_filename', models.CharField(blank=True, max_length=255)),
                ('original_size_bytes', models.PositiveBigIntegerField(default=0)),
                ('result_filename', models.CharField(blank=True, max_length=255)),
                ('result_size_bytes', models.PositiveBigIntegerField(default=0)),
                ('result_relpath', models.CharField(blank=True, max_length=255)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                            related_name='jobs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='Profile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                               related_name='profile', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name='conversionjob',
            index=models.Index(fields=['tool_slug', 'created_at'], name='pdf_tools_c_tool_sl_1a2b3c_idx'),
        ),
        migrations.AddIndex(
            model_name='conversionjob',
            index=models.Index(fields=['status', 'expires_at'], name='pdf_tools_c_status_4d5e6f_idx'),
        ),
    ]

# -*- coding: utf-8 -*-
# Generated by Django 1.9 on 2016-06-03 18:28
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('guidedmodules', '0002_auto_20160521_1927'),
    ]

    operations = [
        migrations.AlterField(
            model_name='task',
            name='project',
            field=models.ForeignKey(help_text='The Project that this Task is a part of, or empty for Tasks that are just directly owned by the user.', on_delete=django.db.models.deletion.PROTECT, to='siteapp.Project'),
        ),
    ]

#!/usr/bin/env python3
"""Link the isolated trip card fixture using an existing native --ru-preview build."""
from pathlib import Path
import shlex
import subprocess

root = Path(__file__).resolve().parents[2]
ninja = (root / '.native-build/pc/build.ninja').read_text().splitlines()
commands = [line.strip()[len('command = '):] for line in ninja if line.strip().startswith('command = ')]
source = 'tools/trip_stats/preview.cc'
obj = '.native-build/pc/obj/trip-stats-preview.o'
output = '.native-build/pc/trip-stats-preview'
compile_command = commands[0].replace('$in', source).replace('$out', obj)
link_inputs = next(line.partition(': link ')[2] for line in ninja if line.startswith('build .native-build/pc/ru-preview: link '))
objects = [value for value in shlex.split(link_inputs) if not value.endswith('/tools_ui_build_ru_preview.cc.o')]
objects.append(obj)
link_command = commands[1].replace('$in', shlex.join(objects)).replace('$out', output)
subprocess.run(shlex.split(compile_command), cwd=root, check=True)
subprocess.run(shlex.split(link_command), cwd=root, check=True)
print(output)

# Stage 14 probe script for run_python_file validation.
# Runs inside UE's embedded Python; the unreal module is implicit.
import unreal

actors = unreal.EditorLevelLibrary.get_all_level_actors()
print(f"probe.py: counted {len(actors)} actors in level")

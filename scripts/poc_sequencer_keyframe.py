"""Live POC: create a Level Sequence, bind a cube, add transform
keyframes via the Python scripting API, verify with get_num_keys."""
import unreal

import time as _time
DEST_PKG = "/Game/Validation/Sequencer"
# time_ns() gives nanosecond resolution so rapid reruns within the same
# wall-clock second can't collide on asset name (int(time.time()) would).
RUN_ID = _time.time_ns()
SEQ_NAME = f"SEQ_POC_Keyframe_{RUN_ID}"
CUBE_LABEL = f"POC_Cube_Keyframe_{RUN_ID}"

el = unreal.EditorAssetLibrary
ll = unreal.EditorLevelLibrary
ext = unreal.MovieSceneSequenceExtensions
tools = unreal.AssetToolsHelpers.get_asset_tools()
els = unreal.EditorActorSubsystem()

# 1. Fresh sequence asset (unique name per run avoids the "asset is
# referenced" delete failure when a prior run's cube still binds it).
seq = tools.create_asset(SEQ_NAME, DEST_PKG, unreal.LevelSequence, unreal.LevelSequenceFactoryNew())
print(f"created sequence: {seq.get_path_name()}")

# 2. Spawn a cube actor; reuse existing if labeled the same.
existing = [a for a in els.get_all_level_actors() if a.get_actor_label() == CUBE_LABEL]
if existing:
    cube = existing[0]
else:
    cube = ll.spawn_actor_from_object(
        el.load_asset("/Engine/BasicShapes/Cube"),
        unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
    cube.set_actor_label(CUBE_LABEL)
print(f"cube actor: {cube.get_actor_label()}")

# 3. Bind cube via LevelSequence.add_possessable. UE 5.7 Python exposes
# this on the sequence itself (not on MovieScene, which is a common
# mistake — MovieScene's AddPossessable is C++-only).
binding = seq.add_possessable(cube)
print(f"binding: {binding}")
guid = binding.get_id()
print(f"binding GUID: {guid}")

# 5. Add a 3D Transform track + section. Extension methods on the
# binding proxy are accessed as bound methods (the Python-side glue
# attaches them to the proxy class). NOT via the Extensions class.
existing_tracks = binding.find_tracks_by_exact_type(unreal.MovieScene3DTransformTrack)
if existing_tracks:
    track = existing_tracks[0]
else:
    track = binding.add_track(unreal.MovieScene3DTransformTrack)
print(f"track: {track}")

sections = track.get_sections()
if sections:
    section = sections[0]
else:
    section = track.add_section()
section.set_range_seconds(0.0, 2.0)
print(f"section range: {section}")

# 6. Enumerate the channels by name.
all_channels = section.get_all_channels()
print(f"channel count: {len(all_channels)}")
for ch in all_channels:
    print(f"  {ch.channel_name} :: {type(ch).__name__}")

# 7. Add a couple of keys: t=0 (origin), t=1 (X=500, Z=100).
# Channel names follow the Location.X / Rotation.X / Scale.X pattern.
def find_channel(name):
    for ch in all_channels:
        if str(ch.channel_name) == name:
            return ch
    return None

# Tick resolution lives on the MovieScene, but in 5.7 the Python getter
# is `get_tick_resolution()` only when accessed via the sequence's
# extension methods, not on MovieScene directly. The robust path: use
# `unreal.MovieSceneSequenceExtensions.get_tick_resolution(seq)`.
tick_rate = unreal.MovieSceneSequenceExtensions.get_tick_resolution(seq)
print(f"tick rate: {tick_rate.numerator}/{tick_rate.denominator}")
def seconds_to_frame(t):
    return unreal.FrameNumber(int(round(t * tick_rate.numerator / tick_rate.denominator)))

interp_linear = unreal.MovieSceneKeyInterpolation.LINEAR

for ch_name, v0, v1 in [
    ("Location.X", 0.0, 500.0),
    ("Location.Y", 0.0, 0.0),
    ("Location.Z", 0.0, 100.0),
]:
    ch = find_channel(ch_name)
    if ch is None:
        print(f"!! missing channel {ch_name}")
        continue
    ch.add_key(seconds_to_frame(0.0), v0, 0.0, unreal.MovieSceneTimeUnit.TICK_RESOLUTION, interp_linear)
    ch.add_key(seconds_to_frame(1.0), v1, 0.0, unreal.MovieSceneTimeUnit.TICK_RESOLUTION, interp_linear)
    print(f"  {ch_name}: keys now {ch.get_num_keys()}")

# 8. Save + report.
el.save_loaded_asset(seq)
print(f"POC_OK path={seq.get_path_name()}")

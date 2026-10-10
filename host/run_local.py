"""Run speaker effects through the original audio engine."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from prepare_settings import prepare

def wine_path(path):
    return 'Z:' + str(path).replace('/', '\\')
from desktop_audio import DesktopAudio, OutputUnavailable, device_settings, supported_speaker

def linked_channels(listing, sink, target, monitor=None, render=True):
    """Require both directed stereo connections, including port identities."""
    edges = set()
    source = None
    for line in listing.splitlines():
        if line and (not line[0].isspace()):
            source = line.strip()
        elif source and line.strip().startswith('|-> '):
            edges.add((source, line.strip()[4:]))
    expected = {(f'{monitor or sink}:monitor_{ch}', f'{sink}_capture:input_{ch}') for ch in ('FL', 'FR')}
    if render:
        expected |= {(f'{sink}_render:output_{ch}', f'{target}:playback_{ch}') for ch in ('FL', 'FR')}
    return expected <= edges

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--dll', type=Path, required=True)
    parser.add_argument('--settings', type=Path, required=True)
    parser.add_argument('--state-reader', type=Path, help='Native pulse_state executable; defaults to the host executable directory')
    parser.add_argument('--target', required=True, help='Exact physical speaker node name')
    parser.add_argument('--profile', choices=('Music', 'Movie', 'Gaming', 'Communication'))
    parser.add_argument('--reuse-session', type=Path, help='Previous local session.json; retain its original runtime settings')
    parser.add_argument('--duration', type=float, help='Optional bounded local test, in seconds')
    parser.add_argument('--state-dir', type=Path, help='Owned persistent application runtime')
    args = parser.parse_args()
    if args.state_dir:
        if args.reuse_session or args.profile:
            parser.error('Persistent runtime manages its own session and profile')
        args.state_dir = args.state_dir.resolve(strict=True)
        if (args.state_dir / '.nahimic-session').read_text() != 'nahimic-linux-v1\n':
            raise ValueError('Not an owned Nahimic runtime')
        saved = args.state_dir / 'session.json'
        if saved.exists() and json.loads(saved.read_text()).get('settings_initialized'):
            args.reuse_session = saved
    if args.duration is not None and args.duration <= 0:
        parser.error('Duration must be positive')
    if args.reuse_session and args.profile:
        parser.error('Select the saved profile through apo_control when reusing a session')
    exe, dll, original = (p.resolve(strict=True) for p in (args.exe, args.dll, args.settings))
    state_reader = (args.state_reader or exe.parent / 'pulse_state').resolve(strict=True)
    sinks = json.loads(subprocess.run(['pactl', '--format=json', 'list', 'sinks'], check=True, capture_output=True, text=True, timeout=10).stdout)
    matches = [s for s in sinks if s['name'] == args.target]
    if len(matches) != 1:
        raise OutputUnavailable('The configured speaker output is unavailable')
    target = matches[0]
    if not supported_speaker(target):
        raise RuntimeError('This launcher only runs on a supported built-in speaker endpoint')
    device_file = device_settings(target)
    dll_sha256 = hashlib.sha256(dll.read_bytes()).hexdigest()
    if args.reuse_session:
        previous_path = args.reuse_session.resolve(strict=True)
        work = previous_path.parent
        if previous_path.name != 'session.json' or (not args.state_dir and (work.parent != Path('/tmp') or not work.name.startswith('nahimic-local-'))):
            raise ValueError('Expected a previous /tmp/nahimic-local-*/session.json')
    else:
        work = args.state_dir or Path(tempfile.mkdtemp(prefix='nahimic-local-', dir='/tmp'))
    session_lock = (work / 'launcher.lock').open('a')
    fcntl.flock(session_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.reuse_session:
        previous = json.loads(previous_path.read_text())
        if not previous.get('settings_initialized') or previous.get('target') != args.target or previous.get('original_dll_sha256') != dll_sha256:
            raise ValueError('Session settings, original DLL or physical target do not match')
    settings = work / 'settings'
    if not args.reuse_session:
        if settings.exists():
            settings.rename(work / ('settings-previous-' + str(time.time_ns())))
        prepare(original, settings)
    device = ET.parse(settings / 'Devices' / device_file).findtext('Data/ID/Value')
    profile_name = args.profile or 'Music'
    profile = ET.parse(settings / 'AudioProfiles' / f'{profile_name}.nsx').findtext('Data/ID/Value')
    if not device or not profile:
        raise ValueError('Original device or profile identifier missing')
    env = os.environ | {'WINEPREFIX': str(work / 'prefix'), 'WINEDEBUG': '-all', 'WINEDLLOVERRIDES': 'mscoree,mshtml='}
    sink = 'nahimic_speakers' if args.state_dir else work.name.replace('-', '_')
    children, files = ({}, [])
    module = None
    desktop = None
    stopping = False
    state = {'pid': os.getpid(), 'target': args.target, 'profile': None if args.reuse_session else profile_name, 'reused_settings': bool(args.reuse_session), 'settings_initialized': bool(args.reuse_session), 'original_dll_sha256': dll_sha256, 'sink': sink, 'work': str(work), 'target_before': target, 'ready': False}
    state_path = work / 'session.json'

    def save():
        pending = state_path.with_suffix('.pending')
        pending.write_text(json.dumps(state, indent=2) + '\n')
        pending.replace(state_path)

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    def start(name, command, **kwargs):
        log = (work / f'{name}.log').open('wb')
        files.append(log)
        child = subprocess.Popen(command, env=env, stderr=log, **kwargs)
        children[name] = child
        return child
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if args.reuse_session:
        history = work / 'runs' / str(time.time_ns())
        history.mkdir(parents=True)
        previous_files = [state_path, *work.glob('*.log')]
        volume_state = work / 'native-volume.state'
        if volume_state.exists():
            previous_files.append(volume_state)
        for previous_file in previous_files:
            previous_file.rename(history / previous_file.name)
        state['previous_run'] = str(history)
    save()
    print(f'Session: {state_path}', flush=True)
    success = False
    try:
        if not args.reuse_session:
            with (work / 'prefix-init.log').open('wb') as log:
                subprocess.run(['wineboot', '--init'], env=env, stdout=log, stderr=log, timeout=90, check=True)
        volume_path = work / 'native-volume.state'
        volume = start('volume', [str(state_reader), args.target, str(volume_path)], stdout=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while 'volume_state_ready' not in (work / 'volume.log').read_text(errors='replace'):
            if volume.poll() is not None:
                raise RuntimeError(f"Native endpoint state initialization failed; see {work / 'volume.log'}")
            if stopping:
                return 0
            if time.monotonic() > deadline:
                raise TimeoutError('Native endpoint state initialization')
            time.sleep(0.05)
        state['volume_state'] = str(volume_path)
        configuration = ['--use-existing-settings'] if args.reuse_session else ['--settings-root', wine_path(settings), '--device-file', device_file, '--device-id', device, '--profile-id', profile]
        host = start('host', ['wine', str(exe), wine_path(dll), '--wine-setup-compat', '--class', 'CHAIN', *configuration, '--pulse-target', args.target, '--volume-state', wine_path(volume_path), '--stdio'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
        deadline = time.monotonic() + 45
        while 'stream_ready' not in (work / 'host.log').read_text(errors='replace'):
            if stopping:
                return 0
            if host.poll() is not None:
                raise RuntimeError(f"Original APO initialization failed; see {work / 'host.log'}")
            if time.monotonic() > deadline:
                raise TimeoutError('Original APO initialization')
            time.sleep(0.05)
        state['settings_initialized'] = True
        save()
        gateway_properties = (f"device.description='Nahimic Speakers' device.class=filter node.virtual=true "
                              f"priority.session=0 node.link-group={sink} filter.smart=true "
                              f"filter.smart.name={sink} filter.smart.disabled=true "
                              "filter.smart.target=" + json.dumps(json.dumps({"node.name": args.target})))
        if args.state_dir:
            gateway_properties += ' monitor.channel-volumes=false'
        else:
            gateway_properties = "device.description='Nahimic Speakers' node.virtual=true priority.session=0"
        created = subprocess.run(['pactl', 'load-module', 'module-null-sink', f'sink_name={sink}', 'format=float32le', 'rate=48000', 'channels=2', 'channel_map=front-left,front-right', f'sink_properties={json.dumps(gateway_properties)}'], check=True, capture_output=True, text=True, timeout=10)
        module = int(created.stdout.strip())
        state['module'] = module
        capture_sink = sink
        common = ['pw-cat', '--raw', '--format', 'f32', '--rate', '48000', '--channels', '2', '--channel-map', 'FL,FR', '--latency', '1024']
        render = start('render', ['pacat', '--playback', '--raw', '--format=float32le', '--rate=48000', '--channels=2', '--channel-map=front-left,front-right', '--latency-msec=80', '--process-time-msec=10', '--device', args.target, '--client-name=Nahimic playback', f'--property=node.name={sink}_render', f'--property=node.link-group={sink}', '--property=node.linger=true', '--property=node.dont-fallback=true'], stdin=host.stdout, stdout=subprocess.DEVNULL)
        host.stdout.close()
        capture = start('capture', common + ['--record', '--target', capture_sink, '--properties', f'{{ node.name = {sink}_capture stream.capture.sink = true node.dont-reconnect = true }}', '-'], stdin=subprocess.DEVNULL, stdout=host.stdin)
        host.stdin.close()
        if args.state_dir:
            desktop = DesktopAudio(work, sink, args.target)
        deadline = time.monotonic() + 10
        while True:
            initialized = desktop is None or desktop.tick(initializing=True)
            listing = subprocess.run(['pw-link', '-l'], check=True, capture_output=True, text=True, timeout=5).stdout
            if initialized and linked_channels(listing, sink, args.target, capture_sink, render=desktop is None or desktop.enabled):
                state['links_at_start'] = listing
                break
            if time.monotonic() > deadline:
                raise TimeoutError('Local audio links did not become ready')
            for name, child in children.items():
                if child.poll() is not None:
                    raise RuntimeError(f'{name} exited before audio links were ready')
            time.sleep(0.1)
        state['ready'] = True
        save()
        selection = 'saved settings' if args.reuse_session else profile_name
        print(f'Ready: {sink} -> original {selection} chain -> {args.target}', flush=True)
        started = time.monotonic()
        while not stopping and (args.duration is None or time.monotonic() - started < args.duration):
            if desktop is not None:
                desktop.tick()
            for name, child in children.items():
                if child.poll() is not None:
                    raise RuntimeError(f'{name} exited unexpectedly with {child.returncode}')
            time.sleep(0.2)
        success = True
        return 0
    except RuntimeError:
        current_sinks = json.loads(subprocess.run(['pactl', '--format=json', 'list', 'sinks'], check=True, capture_output=True, text=True, timeout=5).stdout)
        if not any(s['name'] == args.target and supported_speaker(s) for s in current_sinks):
            success = True
            raise OutputUnavailable('Waiting for the configured built-in speakers') from None
        raise
    finally:
        state['ready'] = False
        save()
        cleanup_error = None
        if desktop is not None:
            try:
                desktop.close()
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                cleanup_error = str(error)
                state['routing_cleanup_error'] = cleanup_error
                success = False
                print(f'Desktop routing cleanup failed: {error}', flush=True)
        for child in reversed(list(children.values())):
            if child.poll() is None:
                child.terminate()
        for child in reversed(list(children.values())):
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        if module is not None:
            subprocess.run(['pactl', 'unload-module', str(module)], check=True, timeout=10)
        for file in files:
            file.close()
        if args.state_dir:
            subprocess.run(['wineserver', '-k'], env=env, check=True, timeout=10)
            subprocess.run(['wineserver', '-w'], env=env, check=True, timeout=10)
        state.update(ready=False, stopped=True, clean_shutdown=success, exit_codes={name: child.returncode for name, child in children.items()})
        save()
        print(f'Stopped; evidence: {state_path}', flush=True)
        if cleanup_error:
            raise RuntimeError(cleanup_error)
if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except OutputUnavailable as error:
        print(str(error), flush=True)
        raise SystemExit(75)

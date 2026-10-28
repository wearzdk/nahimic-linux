import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from desktop_audio import DesktopAudio, OutputUnavailable, device_settings, headphones_plugged, supported_speaker
from run_local import linked_channels


class RoutingTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir='/tmp', prefix='nahimic-routing-')
        self.addCleanup(self.directory.cleanup)
        self.work = Path(self.directory.name)
        self.preference(True)
        self.physical = {'name': 'speaker', 'index': 10, 'description': 'Speaker',
                         'active_port': '[Out] Speaker', 'mute': False,
                         'volume': {ch: {'value': 32768} for ch in ('front-left', 'front-right')},
                         'properties': {'alsa.components': 'HDA:14f11f87,1d05e022,00100100',
                                        'device.bus_path': 'pci-0000:c1:00.6'}}
        self.card = {'name': 'card', 'properties': {'device.bus_path': 'pci-0000:c1:00.6'},
                     'ports': {
                         '[Out] Speaker': {'name': '[Out] Speaker', 'type': 'Speaker',
                                           'availability': 'availability unknown'},
                         '[Out] Headphones': {'name': '[Out] Headphones', 'type': 'Headphones',
                                              'availability': 'not available'},
                         '[In] Headset': {'name': '[In] Headset', 'type': 'Headset',
                                          'availability': 'available'}}}
        self.cards = [self.card]
        self.virtual = {'name': 'effect', 'index': 20, 'properties': {
            'object.id': '30', 'filter.smart': 'true', 'monitor.channel-volumes': 'false',
            'filter.smart.target': json.dumps({'node.name': 'speaker'})}}
        self.streams = [{'index': 1, 'sink': 10, 'properties': {'node.name': 'effect_render'}},
                        {'index': 2, 'sink': 20, 'properties': {'node.name': 'Music'}},
                        {'index': 3, 'sink': 40, 'properties': {'node.name': 'Headset call'}}]
        self.sinks = [self.physical, self.virtual]
        self.router = DesktopAudio(self.work, 'effect', 'speaker')
        self.calls = []
        def pulse(*args):
            self.calls.append(args)
            if args == ('--format=json', 'list', 'sinks'):
                return json.dumps(self.sinks)
            if args == ('--format=json', 'list', 'sink-inputs'):
                return json.dumps(self.streams)
            if args == ('--format=json', 'list', 'cards'):
                return json.dumps(self.cards)
            self.fail('Routing must not change defaults, stream targets or device volume: ' + str(args))
        self.pulse = patch('desktop_audio.pulse', side_effect=pulse)
        self.pulse.start()
        self.addCleanup(self.pulse.stop)
        self.command = patch('desktop_audio.subprocess.run')
        self.run = self.command.start()
        self.addCleanup(self.command.stop)

    def preference(self, enabled):
        (self.work / 'preferences.json').write_text(json.dumps({'enabled': enabled}))

    def test_speaker_filter_preserves_other_device_streams_and_volume(self):
        before = copy.deepcopy(self.streams)
        self.router.tick()
        self.router.tick()
        state = json.loads((self.work / 'desktop-state.json').read_text())
        self.assertTrue(state['active'])
        self.assertEqual(state['applications'], 1)
        self.assertEqual(state['volume'], 50)
        self.assertEqual(self.streams, before)
        self.assertEqual(self.run.call_count, 1)

    def test_switch_and_shutdown_only_change_filter_policy(self):
        self.router.tick()
        self.preference(False)
        self.streams[1]['sink'] = 10  # WirePlumber's bypass result
        self.router.tick()
        self.assertFalse(json.loads((self.work / 'desktop-state.json').read_text())['active'])
        self.preference(True)
        self.router.tick()
        self.router.close()
        self.router.close()
        values = [c.args[0][5] for c in self.run.call_args_list]
        self.assertEqual(values, ['false', 'true', 'false', 'true'])

    def test_uses_actual_stream_destination_not_system_default(self):
        self.streams[1]['sink'] = 40
        self.router.tick()
        self.assertFalse(json.loads((self.work / 'desktop-state.json').read_text())['active'])

    def test_speaker_removal_and_port_change_are_not_processing_targets(self):
        self.sinks.remove(self.physical)
        with self.assertRaises(OutputUnavailable): self.router.tick()
        self.sinks.append(self.physical)
        self.physical['active_port'] = '[Out] Headphones'
        with self.assertRaises(OutputUnavailable): self.router.tick()
        self.run.assert_not_called()

    def test_plugged_headphone_jack_stops_effects_even_when_speaker_port_stays_active(self):
        # ALSA auto-mute can move playback to the headphone pin while the sink
        # still reports [Out] Speaker and keeps the effects attached.
        self.card['ports']['[Out] Headphones']['availability'] = 'available'
        self.assertTrue(headphones_plugged(self.physical, self.cards))
        self.assertFalse(supported_speaker(self.physical, self.cards))
        with self.assertRaises(OutputUnavailable): self.router.tick()
        self.run.assert_not_called()
        self.card['ports']['[Out] Headphones']['availability'] = 'not available'
        self.router.tick()

    def test_headphone_jack_on_a_different_card_does_not_stop_effects(self):
        usb = {'name': 'usb', 'properties': {'device.bus_path': 'usb-0000:00:14.0-1'},
               'ports': {'[Out] Headphones': {'name': '[Out] Headphones', 'type': 'Headphones',
                                              'availability': 'available'}}}
        self.cards.append(usb)
        self.assertTrue(supported_speaker(self.physical, self.cards))
        self.router.tick()
        self.assertTrue(json.loads((self.work / 'desktop-state.json').read_text())['active'])

    def test_each_verified_hardware_keeps_its_own_device_settings(self):
        alc256 = {'name': 'speaker', 'active_port': '[Out] Speaker',
                  'properties': {'alsa.components': 'HDA:10ec0256,1d05e004,00100002'}}
        self.assertEqual(device_settings(alc256), '1D05E004_InternalSpeakers.nsx')
        self.assertTrue(supported_speaker(alc256, self.cards))
        alc256['active_port'] = '[Out] Headphones'
        self.assertFalse(supported_speaker(alc256, self.cards))
        self.assertEqual(device_settings(self.physical), '1D05E022_Speakers.nsx')
        hdmi = {'active_port': '[Out] Speaker',
                'properties': {'alsa.components': 'HDA:1002aa01,00aa0100,00100900'}}
        self.assertIsNone(device_settings(hdmi))
        self.assertFalse(supported_speaker(hdmi, self.cards))

    def test_invalid_target_properties_are_rejected(self):
        self.virtual['properties']['filter.smart.target'] = '{}'
        with self.assertRaises(RuntimeError): self.router.tick()
        self.run.assert_not_called()

    def test_wrong_renderer_target_is_reported(self):
        self.streams[0]['sink'] = 40
        with self.assertRaises(RuntimeError): self.router.tick()

    def test_startup_waits_for_renderer_but_runtime_loss_is_reported(self):
        self.streams.pop(0)
        self.assertFalse(self.router.tick(initializing=True))
        with self.assertRaises(RuntimeError): self.router.tick()

    def test_bypass_still_requires_both_capture_channels(self):
        graph = ('effect:monitor_FL\n  |-> effect_capture:input_FL\n'
                 'effect:monitor_FR\n  |-> effect_capture:input_FR\n')
        self.assertTrue(linked_channels(graph, 'effect', 'speaker', render=False))
        self.assertFalse(linked_channels(graph, 'effect', 'speaker'))
        self.assertFalse(linked_channels(graph.replace('input_FR', 'input_FL'),
                                         'effect', 'speaker', render=False))


if __name__ == '__main__': unittest.main()

class MsiLineOutTest(unittest.TestCase):
    def sink(self, port='analog-output-lineout', components='HDA:10ec1220,1462cc35,00100101'):
        return {'active_port': port, 'properties': {'alsa.components': components},
                'ports': [{'name': 'analog-output-headphones', 'type': 'Headphones',
                           'availability': 'not available'}]}

    def test_exact_lineout_uses_apo3_external_profile(self):
        from desktop_audio import runtime_files
        sink = self.sink()
        self.assertTrue(supported_speaker(sink, []))
        self.assertEqual(device_settings(sink), 'Generic_ExternalSpeakers.nsx')
        self.assertEqual(runtime_files(sink), ('vendor-apo3/NAHIMICV3apo.dll',
                         'vendor-apo3/NAHIMICV3NSControl.dll', 'factory-apo3'))

    def test_headphones_and_other_boards_are_excluded(self):
        self.assertFalse(supported_speaker(self.sink('analog-output-headphones'), []))
        self.assertFalse(supported_speaker(self.sink(components='HDA:10ec1220,1462cc36,00100101'), []))
        sink = self.sink()
        sink['ports'][0]['availability'] = 'available'
        self.assertFalse(supported_speaker(sink, []))

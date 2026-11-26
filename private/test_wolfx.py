"""Isolated provider checks; never import/start the production app or send alerts."""
import ast
import copy
from datetime import datetime
import gzip
import io
import json
from pathlib import Path
import sys
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import timezone, timedelta
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'private'))
import wolfx
from enums import eng_codes


def parser():
    tree = ast.parse((ROOT / 'private/main.py').read_text(encoding='utf-8'))
    names = {'process_data', '_extract_coord', '_extract_area_intensities', '_resolve_bound',
             '_is_eew_canceled', '_eew_cancel_output', '_is_flash_report', '_is_plum_report',
             '_is_volcanic_origin_report', '_max_shindo_from_stations', '_safe_ts',
             '_tsunami_forecasts_in_force', '_tsunami_warning_level', '_is_tsunami_all_clear',
             '_safe_float', '_summarize_obs_region'}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace = {'datetime': datetime, 'time': time, 'eng_codes': eng_codes,
                 'dmdws': Mock(), 'volcanic_origin_event_ids': set(),
                 '_INT_RANK': {v: i for i, v in enumerate(('0','1','2','3','4','5-','5+','6-','6+','7'))},
                 'TSUNAMI_LIFTED_KINDS': {'50','60','00'},
                 'TSUNAMI_KIND_LEVEL': {'52':'Major Warning','53':'Major Warning','51':'Warning','62':'Advisory','71':'Slight sea level change'},
                 '_TSUNAMI_LEVEL_ORDER': ['Major Warning','Warning','Advisory','Slight sea level change']}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<main functions>', 'exec'), namespace)
    return namespace['process_data']


class WolfxTests(unittest.TestCase):
    def setUp(self):
        self.conn = wolfx.Connection()
        self.listener = Mock()
        self.conn.set_message_listener(self.listener)
        self.process = parser()
        self.eew = {'type': 'jma_eew', 'EventID': '20261117123456', 'Serial': 2,
                    'AnnouncedTime': '2026/11/17 12:35:10', 'OriginTime': '2026/11/17 12:34:56',
                    'Hypocenter': '\u5343\u8449\u770c\u5317\u6771\u90e8',
                    'Latitude': 35.7, 'Longitude': 140.7, 'Magnitude': 5.2, 'Depth': 50,
                    'MaxIntensity': '5-', 'isWarn': True, 'isFinal': False,
                    'WarnArea': [{'Chiiki': '\u5343\u8449\u770c\u5317\u6771\u90e8', 'Shindo1': '5-', 'Shindo2': '4', 'Type': 'Warning'}]}
        self.report = {'id': 'r1', 'product': 'VXSE53', 'status': 'issued',
                       'time': '2026-11-17T03:38:00Z', 'originTime': '2026-11-17T03:34:56Z',
                       'hypocenter': 0, 'magnitude': 5.2,
                       'observation': {'maxIntensity':'5-',
                                       'prefs':[{'code':'12','name':'Chiba'}],
                                       'areas':[{'code':'340','name':'NE Chiba','int':'5-'}],
                                       'cities':[{'code':'city','area':0,'stations':[{'code':'1241030','int':'5-'}]}]}}
        self.event = {'eventId': '20261117123456', 'hypocenters': [
            {'name':'NE Chiba','code':'340','latitude':35.7,'longitude':140.7,'depth':50}],
                      'reports':[self.report]}
        self.tsunami = {'type':'jma_tsunami','trusted': True,'status':'active',
                        'validUntil':'2026-11-17T04:00:00Z',
                        'report':{'id':'t1','time':'2026-11-17T03:38:00Z','eventIds':['event']},
                        'areas':[{'code':'200','name':'coast','kind':'52','level':'major_warning',
                                  'maxWave':{'height':None,'text':'\u5de8\u5927'},
                                  'firstWave':{'time':'2026-11-17T03:50:00Z'}}]}

    def test_eew_uses_existing_parser_and_region_codes(self):
        output = self.process(wolfx.adapt_eew(self.eew))
        self.assertEqual(output['event_id'], self.eew['EventID'])
        self.assertTrue(output['warning'])
        self.assertEqual(output['magnitude'], 5.2)
        self.assertEqual(output['area_intensities'][0]['code'], '340')
        self.assertEqual(output['area_intensities'][0]['max_int'], '5-')
        self.assertEqual(output['origin_time'], wolfx.timestamp(self.eew['OriginTime']))

    def test_eew_training_cancel_and_assumption(self):
        self.assertIsNone(wolfx.adapt_eew({**self.eew,'isTraining':True}))
        cancelled = self.process(wolfx.adapt_eew({**self.eew,'isCancel':True}))
        self.assertEqual(cancelled, {'type':'eew_clear','key':self.eew['EventID'],'canceled':True})
        output = self.process(wolfx.adapt_eew({**self.eew,'isAssumption':True}))
        self.assertTrue(output['is_plum'])

    def test_level_method_does_not_invent_hypocenter(self):
        output = self.process(wolfx.adapt_eew({**self.eew,'CodeType':'\u6700\u5927\u4e88\u6e2c\u9707\u5ea6\u306e\u307f'}))
        self.assertIsNone(output['lat'])
        self.assertIsNone(output['magnitude'])
        self.assertFalse(output['is_plum'])

    def test_post_event_station_data_and_zero_index(self):
        message = wolfx.adapt_report(self.event,self.report)
        output = self.process(message)
        self.assertEqual(output['lat'],35.7)
        self.assertEqual(output['max_int'],'5-')
        self.assertEqual(message['body']['body']['intensity']['stations'][0]['code'],'1241030')

    def test_missing_hypocenter_and_cancelled_products_skipped(self):
        self.assertIsNone(wolfx.adapt_report(self.event,{**self.report,'hypocenter':None}))
        self.assertIsNone(wolfx.adapt_report(self.event,{**self.report,'status':'cancelled'}))
        self.assertIsNone(wolfx.adapt_report(self.event,{**self.report,'product':'INVALID'}))

    def test_detail_age_and_deduplication(self):
        with patch.object(wolfx.time,'time',return_value=wolfx.timestamp(self.report['time'])+20):
            self.conn._handle_details({'events':[self.event]})
            self.conn._handle_details({'events':[self.event]})
        self.assertEqual(self.listener.call_count,1)
        old = copy.deepcopy(self.event)
        old['eventId']='old'
        with patch.object(wolfx.time,'time',return_value=wolfx.timestamp(self.report['time'])+1000):
            self.conn._handle_details({'events':[old]})
        self.assertEqual(self.listener.call_count,2)
        self.assertTrue(self.listener.call_args.args[0]['_historical'])

    def test_detail_poll_negotiates_gzip_and_decodes_full_response(self):
        # Exercise Requests' actual response decoding without an upstream server.
        # A large JSON body spans multiple decoded chunks, as the real feed does.
        from requests.adapters import BaseAdapter
        from urllib3.response import HTTPResponse

        payload = {'events': [self.event] * 500}
        compressed = gzip.compress(json.dumps(payload).encode('utf-8'))
        seen_headers = []

        class GzipFeed(BaseAdapter):
            def send(self, request, **kwargs):
                seen_headers.append(request.headers['Accept-Encoding'])
                response = wolfx.requests.Response()
                response.status_code = 200
                response.headers = {'Content-Encoding': 'gzip', 'Content-Type': 'application/json'}
                response.raw = HTTPResponse(
                    body=io.BytesIO(compressed), headers=response.headers,
                    preload_content=False, decode_content=False)
                return response

            def close(self):
                pass

        session = wolfx.requests.Session()
        session.mount('https://', GzipFeed())
        self.conn._handle_details = Mock(side_effect=lambda data: self.conn._stop.set())
        with patch.object(wolfx.requests, 'Session', return_value=session):
            self.conn._poll_details()
        self.assertEqual(seen_headers, ['gzip, deflate'])
        self.conn._handle_details.assert_called_once_with(payload)

    def test_tsunami_qualitative_height_and_expiry(self):
        messages = wolfx.adapt_tsunami(self.tsunami,now=wolfx.timestamp('2026-11-17T03:40:00Z'))
        output = self.process(messages[0])
        self.assertEqual(output['warning_level'],'Major Warning')
        self.assertEqual(output['height_conditions'],['\u5de8\u5927'])
        self.assertEqual(output['arrival_times'],[wolfx.timestamp('2026-11-17T03:50:00Z')])
        self.assertEqual(wolfx.adapt_tsunami(self.tsunami,now=wolfx.timestamp('2026-11-17T04:00:00Z')),[])

    def test_only_official_clear_clears(self):
        now=wolfx.timestamp('2026-11-17T03:40:00Z')
        for status in ('unknown','uninitialized','expired'):
            self.assertEqual(wolfx.adapt_tsunami({**self.tsunami,'status':status},now=now),[])
        self.assertEqual(wolfx.adapt_tsunami({**self.tsunami,'trusted':False},now=now),[])
        messages=wolfx.adapt_tsunami({**self.tsunami,'status':'cleared','areas':[]},now=now)
        self.assertEqual(self.process(messages[0]),{'type':'tsunami_clear'})

    def test_heartbeat_and_old_eew(self):
        ws=Mock()
        self.conn._on_message(ws,json.dumps({'type':'heartbeat'}))
        ws.send.assert_called_once_with('ping')
        with patch.object(wolfx.time,'time',return_value=wolfx.timestamp(self.eew['AnnouncedTime'])+200):
            self.conn._on_message(ws,json.dumps(self.eew))
        self.listener.assert_not_called()

    def test_tsunami_renewal_does_not_repeat_alert(self):
        now=wolfx.timestamp('2026-11-17T03:40:00Z')
        with patch.object(wolfx.time,'time',return_value=now):
            self.conn._on_message(Mock(),json.dumps(self.tsunami))
            self.conn._on_message(Mock(),json.dumps({**self.tsunami,'validUntil':'2026-11-17T04:01:00Z'}))
            self.assertNotIn('_source_refresh_only',self.listener.call_args_list[0].args[0])
            self.assertTrue(self.listener.call_args_list[1].args[0]['_source_refresh_only'])
            self.conn._on_message(Mock(),json.dumps({**self.tsunami,'trusted':False}))
            self.assertEqual(self.listener.call_args.args[0]['type'],'source_status')

    def test_coastal_offshore_and_lower_bound_observations(self):
        station={'code':'20001','name':'buoy','maxWave':{'height':1.2,'text':'1.2m\u4ee5\u4e0a'}}
        reports=[{'product':product,'time':'2026-11-17T03:39:00Z','status':'issued',
                  'observed':[{'code':'200','name':'coast','stations':[station]}]}
                 for product in ('VTSE51','VTSE52')]
        messages=wolfx.adapt_tsunami({**self.tsunami,'observations':reports},
                                     now=wolfx.timestamp('2026-11-17T03:40:00Z'))
        coast=self.process(messages[1])
        self.assertEqual(coast['obs_heights'],[1.2])
        self.assertEqual(coast['obs_height_over'],[True])
        offshore=self.process(messages[2])
        self.assertEqual(offshore['obs_codes'],['20001'])
        self.assertEqual(offshore['obs_height_over'],[True])

    def test_provider_configuration_requirements(self):
        source=(ROOT/'private/main.py').read_text(encoding='utf-8')
        block=source[source.index('JMA_PROVIDER ='):source.index('# Public URL of this deployment')]
        for provider,required_count in (('dmdata',3),('wolfx',0)):
            require=Mock(return_value='test-token')
            namespace={'_optional_env':lambda name:provider,'_require_env':require}
            exec(block,namespace)
            self.assertEqual(require.call_count,required_count)
        with self.assertRaises(SystemExit):
            exec(block,{'_optional_env':lambda name:'invalid'})

    def test_freshness_renewal_broadcasts_without_event_side_effects(self):
        tree=ast.parse((ROOT/'private/main.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='on_message')
        ns={'dmdws':Mock(),'process_data':self.process,'store_recent_data':Mock(), 'cancelled_event_ids':set(),
            'send_data_to_all_sockets':Mock(),'store_data':Mock(),'_bg_executor':Mock()}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'<on_message>','exec'),ns)
        message=wolfx.adapt_tsunami(self.tsunami,now=wolfx.timestamp('2026-11-17T03:40:00Z'))[0]
        ns['on_message']({**message,'_source_refresh_only':True})
        ns['send_data_to_all_sockets'].assert_called_once()
        ns['store_data'].assert_not_called()
        ns['_bg_executor'].submit.assert_not_called()

    def test_long_period_is_mapped_to_existing_processed_fields(self):
        report = {**self.report, 'product': 'VXSE62', 'observation': {'maxLongPeriod': '3'}}
        message = wolfx.adapt_report(self.event, report)
        output = self.process(message)
        self.assertEqual(message['head']['type'], 'VXSE53')
        self.assertEqual(output['max_lpgm'], '3')
        self.assertEqual(output['event_id'], self.event['eventId'])
        self.assertEqual(message['wolfx_payload']['report'], report)

    def test_eew_does_not_roll_back_on_cached_http_response(self):
        now = wolfx.timestamp(self.eew['AnnouncedTime'])
        with patch.object(wolfx.time, 'time', return_value=now):
            self.conn._on_message(Mock(), json.dumps(self.eew))
            self.conn._on_message(None, json.dumps({**self.eew, 'Serial': 1}))
        self.assertEqual(self.listener.call_count, 1)

    def test_hypocenter_changes_trigger_recovery_even_if_report_unchanged(self):
        self.conn._handle_details({'events': [self.event]})
        corrected = copy.deepcopy(self.event)
        corrected['hypocenters'][0]['longitude'] = 141.0
        self.conn._handle_details({'events': [corrected]})
        self.assertEqual(self.listener.call_count, 2)

    def test_historical_messages_do_not_broadcast_or_notify(self):
        tree=ast.parse((ROOT/'private/main.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='on_message')
        ns={'dmdws':Mock(),'process_data':self.process,'store_recent_data':Mock(), 'cancelled_event_ids':set(),
            'send_data_to_all_sockets':Mock(),'store_data':Mock(), '_persist_message':Mock(),
            '_bg_executor':Mock()}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'<on_message>','exec'),ns)
        ns['on_message']({**wolfx.adapt_report(self.event,self.report), '_historical': True})
        ns['store_data'].assert_called_once()
        ns['send_data_to_all_sockets'].assert_not_called()
        ns['store_recent_data'].assert_not_called()
        self.assertIs(ns['_bg_executor'].submit.call_args.args[0], ns['_persist_message'])

    def test_station_corrections_and_restart_dedup_keep_database_schema(self):
        tree=ast.parse((ROOT/'private/main.py').read_text(encoding='utf-8'))
        names={'store_data','_extract_and_store_stations','_store_epicenter'}
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        with tempfile.TemporaryDirectory() as directory:
            connection=sqlite3.connect(str(Path(directory)/'isolated.db'))
            self.addCleanup(connection.close)
            # Use the deployed CREATE TABLE definitions, never the live database.
            for table in ('VXSE53','quake_stations','quake_epicenters'):
                for node in ast.walk(tree):
                    if isinstance(node,ast.Constant) and isinstance(node.value,str) and ('CREATE TABLE IF NOT EXISTS '+table+' (') in node.value:
                        connection.execute(node.value)
                        break
            before=connection.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall()

            @contextmanager
            def database():
                with connection:
                    yield connection.cursor()

            ns={'db':Mock(Database=database),'datetime':datetime,
                'JST':timezone(timedelta(hours=9)),'dmdws':Mock(),
                '_invalidate_quake_points_index':Mock(),'cancelled_event_ids':set(),
                '_station_lookup':{'1241030':{'name':'station','lat':35.7,'lon':140.7}}}
            exec(compile(ast.Module(body=nodes,type_ignores=[]),'<storage functions>','exec'),ns)
            event=copy.deepcopy(self.event)
            event['completeness']='complete'
            message=wolfx.adapt_report(event,event['reports'][0])
            output=self.process(message)
            ns['store_data'](output,message)
            self.assertFalse(ns['store_data'](output,message))
            self.assertEqual(connection.execute('SELECT count(*) FROM VXSE53').fetchone()[0],1)
            event['reports'][0]['observation']['cities'][0]['stations'][0]['int']='4'
            corrected=wolfx.adapt_report(event,event['reports'][0])
            ns['store_data'](self.process(corrected),corrected)
            self.assertEqual(connection.execute('SELECT intensity FROM quake_stations').fetchone()[0],'4')
            event['hypocenters'][0]['longitude']=141.0
            corrected=wolfx.adapt_report(event,event['reports'][0])
            ns['store_data'](self.process(corrected),corrected)
            self.assertEqual(connection.execute('SELECT lon FROM quake_epicenters').fetchone()[0],141.0)
            event['reports'][0]['observation']['cities']=[]
            corrected=wolfx.adapt_report(event,event['reports'][0])
            ns['store_data'](self.process(corrected),corrected)
            self.assertEqual(connection.execute('SELECT count(*) FROM quake_stations').fetchone()[0],0)
            self.assertEqual(before,connection.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall())
            connection.close()

    def test_http_recovery_restores_current_state_without_websocket(self):
        session = Mock()
        eew_response, tsunami_response = Mock(), Mock()
        eew_response.json.return_value = self.eew
        tsunami_response.json.return_value = self.tsunami
        session.get.side_effect = [eew_response, tsunami_response]
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        self.conn._stop = Mock(wraps=self.conn._stop)
        with patch.object(wolfx.requests, 'Session', return_value=session), \
             patch.object(wolfx.time, 'time', return_value=wolfx.timestamp(self.eew['AnnouncedTime'])), \
             patch.object(self.conn._stop, 'wait', side_effect=lambda seconds: self.conn._stop.set()):
            self.conn._poll_snapshots()
        self.assertEqual(self.listener.call_count, 2)
        self.assertEqual(self.listener.call_args_list[0].args[0]['head']['type'], 'VXSE45')
        self.assertEqual(self.listener.call_args_list[1].args[0]['head']['type'], 'VTSE41')

    def test_cancelled_event_is_retracted_without_replaying_old_products(self):
        self.conn._handle_details({'events': [{**self.event, 'status': 'cancelled'}]})
        self.assertEqual(self.listener.call_count, 1)
        self.assertEqual(self.listener.call_args.args[0]['type'], 'wolfx_retraction')
        self.assertEqual(self.listener.call_args.args[0]['event_id'], self.event['eventId'])

    def test_parallel_http_and_websocket_delivery_is_deduplicated(self):
        import threading
        def slow_listener(message):
            time.sleep(0.02)
            self.listener(message)
        self.conn.set_message_listener(slow_listener)
        workers = [threading.Thread(target=self.conn._deliver, args=('key', 'revision', {})) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.listener.assert_called_once()


if __name__ == '__main__':
    unittest.main()

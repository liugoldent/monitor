import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from h_core import Store, TradingError
from capital_notifications import Discord, read_webhook, stop_message

HOOK = 'https://discord.com/api/webhooks/123/test-token'


class CapitalNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root/'state.sqlite')
        self.addCleanup(self.store.db.close)

    def test_reads_only_capital_key_without_loading_other_credentials(self):
        env = self.root/'settings.env'
        env.write_text('UNRELATED_SECRET=private\nDISCORD_KGI_H3_MDD_ADD_URL=https://invalid.example\n'
                       'DISCORD_CAPITAL_H3_MDD_ADD_URL="'+HOOK+'"\n',encoding='utf-8-sig')
        with patch.dict('os.environ',{},clear=True):
            self.assertEqual(read_webhook(env),HOOK)
            import os
            self.assertNotIn('UNRELATED_SECRET',os.environ)

    def test_missing_or_invalid_webhook_is_rejected_without_exposing_value(self):
        env = self.root/'settings.env'
        with self.assertRaises(TradingError):read_webhook(env)
        for value in ['', 'http://discord.com/api/webhooks/secret',
                      'https://invalid.example/api/webhooks/secret',
                      'https://user:secret@discord.com/api/webhooks/123']:
            env.write_text('DISCORD_CAPITAL_H3_MDD_ADD_URL='+value,encoding='utf-8')
            with self.assertRaises(TradingError) as error:read_webhook(env)
            self.assertNotIn('secret',str(error.exception))

    def test_delivery_labels_capital_disables_mentions_and_records_status(self):
        response=Mock(status=204)
        opened=Mock()
        opened.__enter__=Mock(return_value=response)
        opened.__exit__=Mock(return_value=False)
        with patch('capital_notifications.urllib.request.urlopen',return_value=opened) as send:
            self.assertTrue(Discord(HOOK,self.store)('【H3 修復進場｜成交確認】\n多 1口'))
        request=send.call_args.args[0]
        payload=json.loads(request.data)
        self.assertIn('【群益 H3',payload['content'])
        self.assertEqual(payload['allowed_mentions'],{'parse':[]})
        self.assertEqual(send.call_args.kwargs['timeout'],5)
        row=self.store.db.execute("SELECT detail FROM audit WHERE event='capital_discord_delivered'").fetchone()
        self.assertEqual(json.loads(row[0])['status'],204)
        self.assertNotIn('test-token',row[0])

    def test_http_failure_returns_without_retry_or_exposing_webhook(self):
        with patch('capital_notifications.urllib.request.urlopen',side_effect=OSError(HOOK)) as send:
            self.assertFalse(Discord(HOOK,self.store)('【H3 修復進場｜啟動】'))
        send.assert_called_once()
        row=self.store.db.execute("SELECT detail FROM audit WHERE event='capital_discord_failed'").fetchone()
        self.assertEqual(json.loads(row[0]),{'error':'OSError'})

    def test_stop_notice_hides_vendor_payload_and_redacts_controlled_reason(self):
        text=stop_message(RuntimeError('password=secret account=private'),pending=True)
        self.assertNotIn('secret',text)
        self.assertNotIn('private',text)
        self.assertIn('True',text)
        text=stop_message(TradingError('controlled private'),pending=False,
                          redact=lambda value:value.replace('private','***'))
        self.assertIn('controlled ***',text)

    def test_capital_recovery_watcher_receives_discord_callback(self):
        from h_monitor import watch
        config=dict(recovery_sizing=True,notification_url=HOOK)
        callback=Mock()
        with patch('h_monitor.Discord',return_value=callback) as factory, \
             patch('h3_recovery_sizing.watch',return_value='shared') as watcher:
            self.assertEqual(watch(config,self.store,broker=Mock()),'shared')
        factory.assert_called_once_with(HOOK,self.store)
        self.assertIs(watcher.call_args.kwargs['notify'],callback)

    def test_paper_does_not_create_or_send_discord_notification(self):
        from h_monitor import watch
        with patch('h_monitor.Discord') as factory,patch('h3_recovery_sizing.watch'):
            watch(dict(recovery_sizing=True),self.store)
        factory.assert_not_called()

    def test_live_login_failure_notifies_without_starting_watcher_or_sending_order(self):
        from h_monitor import main
        source=self.root/'signals.jsonl';source.write_text('',encoding='utf-8')
        broker=Mock()
        broker.login.side_effect=TradingError('controlled login failed')
        broker.redact.side_effect=lambda value:value
        notify=Mock()
        config=dict(contract=Mock(),signal_source=source)
        with patch('h_monitor.BASE',self.root),patch('h_monitor.load_env'), \
             patch('h_monitor.read_config',return_value=config), \
             patch('h_monitor.selected_account',return_value='TEST2899'), \
             patch.dict('os.environ',{'CAPITAL_LOGIN_ID':'TEST','CAPITAL_PASSWORD':'TEST'}), \
             patch('h_monitor.read_webhook',return_value=HOOK), \
             patch('h_monitor.Discord',return_value=notify), \
             patch('capital_broker.CapitalBroker',return_value=broker), \
             patch('h_monitor.watch') as watcher:
            with self.assertRaises(TradingError):main(['--live','--service'])
        notify.assert_called_once()
        self.assertIn('controlled login failed',notify.call_args.args[0])
        self.assertIn('群益',notify.call_args.args[0])
        watcher.assert_not_called()
        broker.send_and_confirm.assert_not_called()


if __name__=='__main__':
    unittest.main()

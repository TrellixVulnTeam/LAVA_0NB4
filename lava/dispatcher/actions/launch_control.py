#!/usr/bin/python
import json
from lava.dispatcher.actions import BaseAction
from lava.dispatcher.config import LAVA_RESULT_DIR, MASTER_STR, LAVA_SERVER_IP
import socket
from threading import Thread
import xmlrpclib

class cmd_submit_results(BaseAction):
    all_bundles = []

    def run(self, server, stream):
        """Submit test results to a launch-control server
        :param server: URL of the launch-control server
        :param stream: Stream on the launch-control server to save the result to
        """
        #Create l-c server connection
        xmlrpc_url = "%s/xml-rpc/" % server
        srv = xmlrpclib.ServerProxy(xmlrpc_url,
                allow_none=True, use_datetime=True)

        client = self.client
        try:
            self.in_master_shell()
        except:
            client.boot_master_image()

        client.run_shell_command(
            'mkdir -p /mnt/root', response = MASTER_STR)
        client.run_shell_command(
            'mount /dev/disk/by-label/testrootfs /mnt/root',
            response = MASTER_STR)
        client.run_shell_command(
            'mkdir -p /tmp/%s' % LAVA_RESULT_DIR, response = MASTER_STR)
        client.run_shell_command(
            'cp /mnt/root/%s/*.bundle /tmp/%s' % (LAVA_RESULT_DIR,
                LAVA_RESULT_DIR), response = MASTER_STR)
        client.run_shell_command('umount /mnt/root', response = MASTER_STR)

        #Upload bundle list-bundle.lst
        client.run_shell_command('cd /tmp/%s' % LAVA_RESULT_DIR,
            response = MASTER_STR)
        client.run_shell_command('ls *.bundle > bundle.lst',
            response = MASTER_STR)

        t = ResultUploader()
        t.start()
        client.run_shell_command(
            'cat bundle.lst |nc %s %d' % (LAVA_SERVER_IP, t.get_port()),
            response = MASTER_STR)
        t.join()

        bundle_list = t.get_data().strip().splitlines()

        #flush the serial log
        client.run_shell_command("")
        serial_log = client.sio.getvalue()

        #Upload bundle files to server
        for bundle in bundle_list:
            t = ResultUploader()
            t.start()
            client.run_shell_command(
                'cat /tmp/%s/%s | nc %s %s' % (LAVA_RESULT_DIR, bundle,
                    LAVA_SERVER_IP, t.get_port()),
                response = MASTER_STR)
            t.join()
            content = t.get_data()

            #attach serial log
            content = self._attach_seriallog(content, serial_log)
            self.all_bundles.append(json.loads(content))
            
        main_bundle = self.combine_bundles()
        srv.put(main_bundle, 'lava-dispatcher.bundle', stream)

    def combine_bundles(self):
        if not self.all_bundles:
            return
        main_bundle = self.all_bundles.pop(0)
        test_runs = main_bundle['test_runs']
        for bundle in self.all_bundles:
            test_runs += bundle['test_runs']
        return json.dumps(main_bundle)

    def _attach_seriallog(self, content, serial_log):
        """
        Add serial log to the "attachments" field, it aligns bundle 1.2 format
        """
        attachment = { 
                "pathname": "serial.log",
                "mime_type": "text/plain",
                "content": serial_log }
        data = json.loads(content)
        for test_run in data["test_runs"]:
            if "attachments" in test_run:
                test_run["attachments"].append(attachment)
            else:
                test_run["attachments"] = [ attachment ]
        return json.dumps(data)

class ResultUploader(Thread):
    """
    Simple HTTP Server for uploading bundles
    """
    def __init__(self):
        """
        if no filename specified, just get uploaded data
        """
        Thread.__init__(self)
        self.data = ""
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.bind(('', 0))

    def get_port(self):
        return self.s.getsockname()[1]

    def get_data(self):
        return self.data

    def run(self):
        self.s.listen(1)
        conn, addr = self.s.accept()
        while(1):
            #10KB per time
            data = conn.recv(10240)
            if not data:
                break
            self.data = self.data + data

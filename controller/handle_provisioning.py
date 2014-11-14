#!/usr/bin/env python

import shutil
import os
import re
import threading
import logging
from time import sleep

import SoftLayer

from .asyncproc import Process
from models.models import db, Cluster


logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s: %(levelname)s: %(filename)s: %(funcName)s(): %(message)s')
logger = logging.getLogger("handle_provisioning")


# # git clone --recursive https://github.com/irifed/vagrant-cluster.git cleanrepo
cleanrepo = '/tmp/vagrant-cluster'
vagrantroot = '/tmp/clusters/cluster'


# stolen from http://stackoverflow.com/questions/4417546/constantly-print-subprocess-output-while-process-is-running
def run_process(command, cluster_id):
    vagrant = Process(command)
    outf = open('vagrant.out', 'w')
    errf = open('vagrant.err', 'w')
    masterip = None
    while True:
        # print any new output to files
        try:
            out, err = vagrant.readboth()
            if out != '':
                outf.write(out)
                outf.flush()
            if err != '':
                errf.write(err)
                errf.flush()
        except Exception as e:
            print(e.args)


        # check to see if process has ended
        poll = vagrant.wait(os.WNOHANG)
        if poll is not None and out == '':
            break

        if 'master: SSH address:' in str(out):
            masterip = out.strip().split(' ')[3]
            print('MASTER IP IS: ' + masterip)
            store_master_ip_and_password(masterip, cluster_id)

        sleep(2)

    outf.close()
    errf.close()

    return masterip


def handle_process_and_write(runcommand, cluster_id):
    master_ip = run_process(runcommand, cluster_id)
    store_master_ip_and_password(master_ip, cluster_id)


def async_run_process(runcommand, cluster_id):
    """execute following on a new thread
    handlepProcessAndWrite(runcommand, curdir)
    """

    process_args = (runcommand, cluster_id)
    t = threading.Thread(target=handle_process_and_write, name='cucumber',
                         args=process_args)
    t.daemon = False
    t.start()


def do_provisioning(cluster_id, cleanrepo, vagrantroot, sl_config):
    curdir = vagrantroot + '.' + cluster_id
    shutil.copytree(cleanrepo, curdir, symlinks=False, ignore=None)
    os.chdir(curdir)

    sl_config.create_sl_config_file(curdir + '/sl_config.yml')

    runcommand = \
        "NUM_WORKERS={} vagrant up --provider=softlayer --no-provision && " \
        "PROVIDER=softlayer vagrant provision".format(sl_config.num_workers)
    logger.debug(runcommand)

    async_run_process(runcommand, cluster_id)


def provision_cluster(cluster_id, sl_config):
    # TODO get rid of this function
    do_provisioning(cluster_id, cleanrepo, vagrantroot, sl_config)


def get_cluster_status(cluster_id):
    cluster_home = vagrantroot + '.' + cluster_id

    stdout = open(cluster_home + '/vagrant.out', 'r')
    stderr = open(cluster_home + '/vagrant.err', 'r')

    # TODO grep out master ip address
    cluster_log = stdout.read()
    cluster_err = stderr.read()

    master_ip = None
    if 'master: SSH address:' in cluster_log:
        master_ip = re.search(
            'master: SSH address: ([0-9]+(?:\.[0-9]+){3})',
            cluster_log).groups()[0]

    return master_ip, cluster_log, cluster_err


def get_master_password_from_sl(master_ip, cluster_id):
    # retrieve sl username and api key by cluster_id
    cluster = Cluster.by_uuid(cluster_id)

    client = SoftLayer.Client(username=cluster.sl_username,
                              api_key=cluster.sl_api_key)

    vs_manager = SoftLayer.managers.VSManager(client)

    # WARNING: this is a very long call for some reason
    # TODO fix this
    master_details = vs_manager.list_instances(public_ip=master_ip)

    master_instance = vs_manager.get_instance(instance_id=master_details[0]['id'])
    master_password = master_instance['operatingSystem']['passwords'][0]['password']

    # store password in db for faster retrieval in the future
    cluster.master_password = master_password
    db.session.commit()

    return master_password


def store_master_ip_and_password(master_ip, cluster_id):
    # TODO verify that master_ip is valid ip

    master_password = get_master_password_from_sl(master_ip, cluster_id)

    cluster = Cluster.by_uuid(cluster_id)
    cluster.master_ip = master_ip
    cluster.master_password = master_password
    db.session.commit()

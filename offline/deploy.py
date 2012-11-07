from fabric.api import task, execute, env, run, sudo, put
from fabric.contrib.files import sed

from cuisine import *

env.puppet_ip= "192.168.2.10"

@task
def install_puppet():
    run("wget http://apt.puppetlabs.com/puppetlabs-release-precise.deb")
    sudo("dpkg -i puppetlabs-release-precise.deb")
    sudo("apt-get update")
    package_ensure("puppet")
    with mode_sudo():
        file_append("/etc/hosts",
                "%s  learn.localdomainlearn puppet.localdomain puppet" % env.puppet_ip)

    sed("/etc/default/puppet", "START=no", "START=yes", use_sudo=True)

@task
def change_hostname(hostname):
    old_hostname= run("hostname")
    sudo('echo "%s" > /etc/hostname' %hostname)
    sed("/etc/hosts", old_hostname, hostname, use_sudo= True)
    sudo("hostname %s"% hostname)

import os
import fabric.contrib.files
from fabric.api import task, execute, env, run, sudo, put
from fabric.utils import puts, warn, error
from fabric.contrib.console import confirm
from fabric.contrib.files import exists
from fabric.context_managers import settings, cd
from contextlib import contextmanager

@contextmanager
def shell_env(**env_vars):
    orig_shell = env['shell']
    env_vars_str = ' '.join('{0}={1}'.format(key, value)
                           for key, value in env_vars.items())
    env['shell']='{0} {1}'.format(env_vars_str, orig_shell)
    yield
    env['shell']= orig_shell

def chroot(cmd):
    return sudo("chroot mnt/ %s" %cmd)

def chins(cmd):
    return sudo("chroot mnt/ apt-get install -y %s" %cmd)

def chbash(cmd):
    return sudo("echo '%s' | sudo bash" %cmd)

def upload_template(filename, dest):
    return fabric.contrib.files.upload_template(filename, dest,
                                         use_jinja=True, template_dir="templates",
                                         backup=False, use_sudo=True)
def root():
    if not env.get("noroot"):
        root= env.get("root") or "ubuntu"
        if not exists(root): run("mkdir -p %s" %root)
        env.noroot= True

        return cd(root)
    return cd(".")

@task
def prepare( size=2000 ):
    with root():
        if exists("root.img"):
            if not confirm("Do you want to create new image?"):
                return
            execute(unmount)

        run("dd if=/dev/zero of=root.img bs=1024k count=%d"% size)
        run("mkfs.ext4 -F -L root root.img")

        if exists("mnt"):
            run("mkdir -p mnt")

@task
def resize( new_size=1800 ):
    with root():
        # mount image without devices, create temp image and copy data
        mount(False)
        run("dd if=/dev/zero of=tmp.img bs=1024k count=%d"% new_size)
        run("mkfs.ext4 -F -L ubuntu tmp.img")
        run("mkdir -p tmp")
        sudo("mount -o loop tmp.img tmp/")
        sudo("cp -rv mnt/* ./tmp/")

        # umount and create rename image
        execute(unmount)
        run("rm root.img")
        sudo("umount tmp.img")
        run("mv tmp.img root.img")

@task
def mount(devices=True):
    with root():
        if not exists("root.img"):
            if confirm("Root image does not seem to exist, create one?"):
                execute(prepare)

        run("mkdir -p mnt")

        execute(unmount)
        run("e2fsck -p root.img")
        sudo("mount -o loop root.img mnt/")
        if devices:
            sudo("mkdir -p mnt/proc")
            sudo("mount -t proc proc mnt/proc")
            sudo("mkdir -p mnt/dev")
            sudo("mount --bind /dev mnt/dev")
            sudo("mkdir -p mnt/sys")
            sudo("mount -t sysfs sysfs mnt/sys")
            sudo("mount -t devpts /dev/pts mnt/dev/pts")

@task
def unmount():
    with root():
        with settings(warn_only=True):
            sudo("sudo lsof -t mnt/ | sudo xargs -r kill")
            sudo("sudo chroot mnt/ /etc/init.d/udev stop")
            sudo("sudo chroot mnt/ /etc/init.d/cron stop")
            sudo("umount mnt/proc")
            sudo("umount mnt/sys")
            sudo("umount mnt/dev/pts")
            sudo("umount mnt/dev")
            sudo("umount mnt/")

@task
def debootstrap(release= None, mirror= None, target_arch= None):
    opts = dict(
            release= release or env.get("release") or "oneiric",
            mirror= mirror or env.get("mirror") or "http://de.archive.ubuntu.com/ubuntu/",
            target_arch= target_arch or env.get("target_arch") or "amd64"
            )

    with root():
        opts["target"]= "debootstrap/%(release)s_%(target_arch)s" % opts
        if not exists(opts["target"]):
            run("mkdir -p %s" %opts["target"])
        puts("""Debootstraping release=%(release)s
            target=%(target)s mirror=%(mirror)s
            target_arch=%(target_arch)s to %(target)s""" % opts)
        with settings(warn_only=True):
            ret= sudo("debootstrap --arch %(target_arch)s %(release)s %(target)s %(mirror)s" % opts)

@task
def install(password= None, start_ssh=True, release= None, target_arch= None,
            install_packages= True):
    opts = dict(
            release= release or env.get("release") or "oneiric",
            target_arch= target_arch or env.get("target_arch") or "amd64",
            password= password or env.get("password") or "root",
            start_ssh= start_ssh or env.get("start_ssh"),
            )

    with root():
        puts("Mounting onyl devices")
        execute(unmount)
        execute(mount,False)
        opts["target"]= "debootstrap/%(release)s_%(target_arch)s" % opts
        if not exists(opts["target"]):
            execute(debootstrap, release=opts["release"], target_arch=opts["target_arch"])
        sudo("cp -rp %(target)s/* ./mnt/" %opts)

        execute(mount)

        puts("Configuring...")
        if not os.path.exists("templates/sources.list"):
            chbash("""cat >> mnt/etc/apt/sources.list <<EOF
deb http://archive.ubuntu.com/ubuntu $(lsb_release -cs) main restricted universe multiverse
deb http://archive.ubuntu.com/ubuntu $(lsb_release -cs)-security main restricted universe multiverse
deb http://archive.ubuntu.com/ubuntu $(lsb_release -cs)-updates main restricted universe multiverse
deb http://archive.canonical.com/ubuntu $(lsb_release -cs) partner
EOF\n
                """)
        else:
            upload_template("sources.list", "mnt/etc/apt/sources.list")

        if not os.path.exists("templates/interfaces"):
            pass
        else:
            upload_template("intefaces", "mnt/etc/network/interfaces")

        sudo("cp /etc/mtab mnt/etc/mtab")
        chbash("""cat >> mnt/etc/apt/apt.conf.d/10periodic <<EOF
APT::Periodic::Enable "1";
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::AutocleanInterval "5";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::RandomSleep "1800";
EOF\n
            """)
        chroot("passwd << EOF\n%(password)s\n%(password)s\nEOF\n" % opts)

        if install_packages:
            with shell_env(DEBIAN_FRONTEND="noninteractive"):
                puts("Installing packages...")
                chroot("apt-get update -y")
                chins("grub-pc")
                chins("linux-image")

                chins("udev")
                chbash("echo \"none /dev/pts devpts defaults 0 0\" >> mnt/etc/fstab")
                chbash("echo \"none /proc proc defaults\" >> mnt/etc/fstab")

                chins("sudo python-software-properties vim nano joe screen \
                      unattended-upgrades smartmontools ntp ssh openssh-server")

                sudo("sudo lsof -t mnt/ | sudo xargs -r kill")

                if opts["start_ssh"]:
                    chbash("sed -i \"s/Port 22/Port 23/g\" mnt/etc/ssh/sshd_config")
                    chroot("/etc/init.d/ssh start")

@task
def flash(fsroot= None, swap= None, home= None):
    opts = dict(
            root= fsroot or env.get("root") or "/dev/sdb1",
            swap= swap or env.get("swap") or "/dev/sdb2",
            home= home or env.get("home") or None
            )

    with root():
        if not exists("mnt/dev"):
            if not exists("root.img"):
                error("Your image does not seem to exist...")

            warn("Your image does not seem to be mounted...")
            if confirm("Should i mount it?"):
                execute(mount)

        puts("Wrinting image: rootfs=%(root)s, swap=%(swap)s, home=%(home)s" %opts)
        if opts["home"]:
            fstab="""cat > mnt/etc/fstab <<EOF
# device mount   type options freq passno
UUID=$(blkid -o value -s UUID root.img) /       ext4 errors=remount-ro,user_xattr 0 1
UUID=$(blkid -o value -s UUID %(swap)s) none    swap    sw                        0 0
UUID=$(blkid -o value -s UUID %(home)s /home   ext4 defaults                     0 0
EOF\n
                """
        else:
            fstab="""cat > mnt/etc/fstab <<EOF
# device mount   type options freq passno
UUID=$(blkid -o value -s UUID root.img) /       ext4 errors=remount-ro,user_xattr 0 1
UUID=$(blkid -o value -s UUID %(swap)s) none    swap    sw                        0 0
EOF\n
                """
        puts("fstab:\n"+fstab)
        chbash(fstab %opts)

        puts("Writing image to flash drive...")
        sudo("dd if=root.img of=%(root)s" %opts)

        puts("Installing grub...")
        chroot("grub-install %s" %opts["root"][:-1])
        chroot("update grub")
        execute(unmount)

        #puts("Writing image back...")
        #sudo("dd if=%(root)s of=root.img")

#!/bin/bash

yum update -y
yum -y install git htop ntf4-acl-tools tmux

## Install python deps
sudo -u ec2-user bash -c "wget https://repo.continuum.io/miniconda/Miniconda2-latest-Linux-x86_64.sh -O \$HOME/miniconda.sh"
sudo -u ec2-user bash -c "bash \$HOME/miniconda.sh -b -p \$HOME/miniconda"
sudo -i -u ec2-user bash -c "echo export PATH=\\\$HOME/miniconda/bin:\\\$PATH >> \$HOME/.bashrc"
sudo -i -u ec2-user conda install -y ipyparallel dill
#sudo -i -u ec2-user conda install -y scipy h5py matplotlib sortedcontainers
#sudo -i -u ec2-user conda install -y -c etetoolkit ete2

## Format and mount EBS data volume
if [ ! -d {ebsdata_mount_point} ]
then
	mkdir {ebsdata_mount_point}
	# You should check if dumpe2fs returns
	# dumpe2fs: Bad magic number in super-block while trying to open /dev/xvdd
	# Couldn't find valid filesystem superblock.
	mke2fs -t ext4 {ebsdata_device}
fi
echo {ebsdata_device} {ebsdata_mount_point} ext4 defaults,auto 0 0 >> /etc/fstab
mount {ebsdata_mount_point}
chmod 777 {ebsdata_mount_point}

## Start NFS share on the EBS data volume
echo /ebsdata {network_prefix}\(rw,sync,fsid=0\) >> /etc/exports
chkconfig nfs on
service nfs start

## Start ipcluster controller
#IP=$(ifconfig eth0 inet | grep inet | awk '{{print $2}}')
IP=$(ifconfig eth0 | grep 'inet addr' | cut -d: -f2 | awk '{{print $1}}')
sudo -i -u ec2-user ipython profile create --parallel --profile-dir={ebsdata_mount_point}/profile_ec2
sudo -i -u ec2-user ipcluster start --profile-dir={ebsdata_mount_point}/profile_ec2 --ip=$IP --n=0 --daemonize=True

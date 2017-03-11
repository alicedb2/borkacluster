#!/bin/bash

yum update -y
yum -y install git htop ntf4-acl-tools

## Install python deps
sudo -u ec2-user bash -c "wget https://repo.continuum.io/miniconda/Miniconda2-latest-Linux-x86_64.sh -O \$HOME/miniconda.sh"
sudo -u ec2-user bash -c "bash \$HOME/miniconda.sh -b -p \$HOME/miniconda"
sudo -i -u ec2-user bash -c "echo export PATH=\\\$HOME/miniconda/bin:\\\$PATH >> \$HOME/.bashrc"
sudo -i -u ec2-user conda install -y ipyparallel dill
#sudo -i -u ec2-user conda install -y scipy h5py matplotlib sortedcontainers
#sudo -i -u ec2-user conda install -y -c etetoolkit ete2

## Mount EBS dataa volume
if [ ! -d {ebsdata_mount_point} ]
then
	mkdir {ebsdata_mount_point}
fi
echo {controller_ip}:/ {ebsdata_mount_point} nfs4 nfsvers=4.1,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2 0 0 >> /etc/fstab
mount {ebsdata_mount_point}

## Start ipcluster controller
sudo -i -u ec2-user ipcluster engines --profile-dir={ebsdata_mount_point}/profile_{cluster_name} --daemonize=True

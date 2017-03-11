# borkacluster
Automatically create a bare-bone [ipyparallel](https://github.com/ipython/ipyparallel) cluster using a spot fleet on [Amazon’s Elastic Compute Cloud (EC2)](https://aws.amazon.com/ec2/)

## Motivation
This is really just a monolithic 'one-size-fits-no-one' automation script that came out while playing with AWS EC2 and the [AWS SDK](https://aws.amazon.com/sdk-for-python/). Do yourself a favor and consider instead [StarCluster](http://star.mit.edu/cluster/) for a serious solution.

## Requirements
* [AWS CLI](https://aws.amazon.com/cli/)
* [Boto3](https://aws.amazon.com/sdk-for-python/)
* [IPython](https://ipython.org/) + [ipyparallel](https://github.com/ipython/ipyparallel)
* [Requests](http://docs.python-requests.org/en/master/)
* [py2-ipaddress](https://pypi.python.org/pypi/py2-ipaddress)
* [Numpy](http://www.numpy.org)

## Usage

```python
from borkacluster import create_cluster, dismantle_cluster
from ipyparallel import Client

cluster = create_cluster(target_number_of_cores=8)
```

```
Borking cluster: bork (8 vCPU)
------------------------------------------------------------
Creating Virtual Private Cloud (VPC) with prefix 10.0.0.0/16...configuring...done
Creating Internet Gateway (IGW)...attaching...done
Fixing route table (RTB)...done
Creating subnets...10.0.0.0/17(ca-central-1a)...10.0.128.0/17(ca-central-1b)...done
Creating security groups...bork_controller...bork_engine...bork_data...done
Configuring security groups...bork_controller...bork_engine...bork_data...done
No controller+EBS Avail. Zone specified, so I chose ca-central-1a for you.
Creating EBS data volume, (controller) /dev/xvdd --> (controller) /ebsdata (16 GiB, gp2)...done
Creating key pair...bork_ca-central-1 already exists and will be used (hope you kept that PEM file somewhere!)...done
Launching controller instance...0:pending...0:pending...0:pending...16:running!
	Controller private IP: 10.0.124.230
	 Controller public IP: 52.60.133.174
Attaching EBS data volume to controller...done
Hold on to your helmet, requesting spot fleet (8 vCPU)...
Loading OnDemand price list...using simplified_price_list.json...done
Interrogating bid advisor...done
	Max spot price bid: 0.0141
	          c4.large: 0.009975
	        c4.8xlarge: 0.011667
	        c4.2xlarge: 0.012398
	        c4.4xlarge: 0.013322
	         c4.xlarge: 0.0141
	At worst this cluster will cost $0.1128/hour.
Placing spot fleet request...done
------------------------------------------------------------
Cluster bork should be up and running in a couple minutes.
```

Monitor your controller instance on the AWS EC2 console. A couple of minutes later, fetch the ipcontroller-client.json file. If you're in IPython you can do something like
```python
!scp -oStrictHostKeyChecking=no -i bork_ca-central-1.pem ec2-user@52.60.133.174:/ebsdata/profile_bork/security/ipcontroller-client.json .

from ipyparallel import Client

bork_client = Client('ipcontroller-client.json', sshserver='ec2-user@52.60.133.174', sshkey='bork_ca-central-1.pem')
lbv = bork_client.load_balanced_view()
lbv.queue_status()
```

```
{0: {u'completed': 0, u'queue': 0, u'tasks': 0},
 1: {u'completed': 0, u'queue': 0, u'tasks': 0},
 2: {u'completed': 0, u'queue': 0, u'tasks': 0},
 3: {u'completed': 0, u'queue': 0, u'tasks': 0},
 4: {u'completed': 0, u'queue': 0, u'tasks': 0},
 5: {u'completed': 0, u'queue': 0, u'tasks': 0},
 6: {u'completed': 0, u'queue': 0, u'tasks': 0},
 7: {u'completed': 0, u'queue': 0, u'tasks': 0},
 u'unassigned': 0}
```

You now have 8 engines with 1 core each at your disposal.

By default each engine mounts the 16 GiB NFS volume shared by the controller instance. Unless explicitly specified this volume is not deleted during the dismantling of the cluster. The default mount point on both engines and controller is /ebsdata

```python
# When you're done with the cluster

dismantle_cluster(cluster)
```

```
Finding fleet instance ids...done
Cancelling fleet request...done
Waiting for fleet instances to terminate...shutting-down (0/4 terminated)...shutting-down (0/4 terminated)...shutting-down (0/4 terminated)...shutting-down (0/4 terminated)...(4/4 terminated)...fleet terminated!
Terminating controller instance...32:shutting-down...32:shutting-down...32:shutting-down...32:shutting-down...32:shutting-down...32:shutting-down...32:shutting-down...32:shutting-down...someone's slow...48:terminated!
Wiping security group permissions...bork_data...bork_engine...bork_controller...done
Deleting security groups...bork_data...bork_engine...bork_controller...done
Deleting subnets...10.0.0.0/17(ca-central-1a)...10.0.128.0/17(ca-central-1b)...done
Deleting internet route...done
Detaching internet gateway...done
Deleting internet gateway...done
Deleting Virtual Private Cloud...done
Cluster bork dismantled!
```

TODO
* Fetch ipcontroller-client.json from controller instance when ready, and setup local ipyparallel profile accordingly
* Reorganize/eliminate redundancy in security group permissions
* Add possibility to attach and share an already existing NFS volume
* Add support for EFS and S3 data storage (will need creation of IAM role)
* Check-point cluster resources more often to make cleaning-up easier in case something goes wrong
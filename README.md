# borkacluster
Automatically create a bare-bone [ipyparallel](https://github.com/ipython/ipyparallel) cluster using a spot fleet on [Amazon’s Elastic Compute Cloud (EC2)](https://aws.amazon.com/ec2/)

## Motivation
This is really just a monolithic 'one-size-fits-no-one' automation script that came out of trying to understand how AWS EC2 and the [AWS SDK](https://aws.amazon.com/sdk-for-python/) work. Do yourself a favor and look at [StarCluster](http://star.mit.edu/cluster/) instead.

## Requirements
* [AWS CLI](https://aws.amazon.com/cli/)
* [Boto3](https://aws.amazon.com/sdk-for-python/)
* [IPython](https://ipython.org/) + [ipyparallel](https://github.com/ipython/ipyparallel)
* [Requests](http://docs.python-requests.org/en/master/)
* [py2-ipaddress](https://pypi.python.org/pypi/py2-ipaddress)
* [Numpy](http://www.numpy.org)

## Usage

```import borkacluster
from ipyparallel import Client

cluster = borkacluster.create_cluster()  # By default creates a cluster named bork with a fleet of 8 vCPU
							   			 # Cluster resources are returned and saved in bork_ClusterResources.json

# For now you'll have to fetch the ipcontroller-client.json file yourself

borkacluster.dismantle_cluster(cluster)

```

TODO: Fetch ipcontroller-client.json from controller instance when ready, and setup local ipyparallel profile accordingly
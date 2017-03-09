from __future__ import print_function
import base64
import boto3
from datetime import datetime, timedelta
import ipaddress
from itertools import count
import json
from numpy import ceil, log2, mean, median, std
import os
from random import choice
import re
import requests
import sys
import time

def _tag_cluster_res(client, cluster_name, resource_ids, resource_type):
	if type(resource_ids) == str:
		resource_ids = [resource_ids]
	return client.create_tags(Resources=resource_ids, Tags=[{'Key':'Name', 'Value':cluster_name + ' ' + resource_type}, {'Key':'Cluster', 'Value':cluster_name}])

def generate_simplified_price_list():
	''' Download Amazon's price list and generate simplified list for OnDemand Linux instances.'''
	pricing_url_prefix = 'https://pricing.us-east-1.amazonaws.com'
	print('Getting latest offers...', end='')
	offers = requests.get(pricing_url_prefix + '/offers/v1.0/aws/index.json')
	offers = offers.json()
	print('done!')
	
	price_list_url = pricing_url_prefix + offers['offers']['AmazonEC2']['currentVersionUrl']
	print('Downloading (100MB+ file)...', end='')
	price_list = requests.get(price_list_url)

	print('generating...', end='')
	price_list = price_list.json()
	simplified_price_dict = dict()
	simplified_price_list = []
	for tv_ in price_list['terms']['OnDemand'].itervalues():
		tv = tv_.values()[0]
		sku = tv['sku']
		price = tv['priceDimensions'].values()[0]
		prod = price_list['products'][sku]
		attr = prod['attributes']

		if (prod['productFamily'] == 'Compute Instance') and (attr['tenancy'] != 'Host') and (attr['operatingSystem'] == 'Linux'):
			if not simplified_price_dict.has_key(attr['instanceType']):
				simplified_price_dict[attr['instanceType']] = dict()
				simplified_price_dict[attr['instanceType']]['Shared'] = dict()
				simplified_price_dict[attr['instanceType']]['Dedicated'] = dict()

			simplified_price_dict[attr['instanceType']][attr['tenancy']][attr['location']] = price['pricePerUnit']['USD']
			simplified_price_list.append((attr['instanceType'], attr['tenancy'], attr['location']) + price['pricePerUnit'].items()[0])

	print('saving...', end='')
	with open('simplified_price_list.json', 'w') as f:
		json.dump(simplified_price_dict, f, indent=1)
	print('done')

def create_cluster(cluster_name='bork', target_number_of_cores=8, bid_style='cheap', cheap_factor=1.5, cluster_region='ca-central-1', controller_availability_zone=None, data_volume_size=16):
	""" Create a computing cluster out of an EC2 spot fleet of Linux instances.

	It may well work 'as-is' and out-of-the-box if ~/.aws/credentials are already configured.
	
	The controller instance (OnDemand t2.micro) will act as an ipyparallel ipcontroller node.
	The controller instance will share a NFS volume of size data_volume_size GiB. This volume will not be deleted when dismantling the cluster.
	The fleet will be constituted of c3.*large and c4.*large instances totalling target_number_of_cores virtual vCPU/cores.
	The spot fleet will try to maintain the target vCPU capacity specified by target_number_of_cores.
	
	The bidding style can be either 'cheap' or 'automatic'.
	
	The cheap bidding uses:
		spot price = cheap_factor * (median spot price per vCPU over last 12 hours 
									 of the most expansive instance type allowed in the fleet)
	Cheap bidding will default back at launch to automatic if cheap_factor is set too high
	or if the median spot price happens to be too high that day due to spikes/dumb bidders.
	
	The automatic style mimick Amazon's way, namely it sets the spot price to the OnDemand price/hours
	of the most expansive/vCPU instance type in the fleet.
	
	Usually the most expansive/vCPU OnDemand instances in a region will either be c3.large or c4.large.

	The controller node will run a startup script given by the template in ipcontroller_config.sh
	The same goes for engine instances with ipengine_config.sh
	You may want to modify the latter in order to install more than a bare miniconda environment on engine instances.
	"""

	if bid_style == 'cheap':
		pass
	elif bid_style == 'automatic':
		pass
	else:
		raise Exception('Bid style must be either \'cheap\' or \'automatic\'.')

	print('Borking cluster: ' + cluster_name)
	print('-'*60)

	cluster = dict()

	#### Creating regional EC2 client
	if cluster_region is None:
		ec2 = boto3.client('ec2')
		regions = [r['RegionName'] for r in ec2.describe_regions()['Regions']]
		region_name = choice(regions)
		print('You really ough to choose a cluster_region yourself...')
		print('but since you didn\'t I chose ' + region_name + ' for you')

	ec2 = boto3.client('ec2', region_name=cluster_region)
	

	cluster['region'] = cluster_region
	cluster['name'] = cluster_name
	availability_zones = [r['ZoneName'] for r in ec2.describe_availability_zones()['AvailabilityZones']]

	### Creating VPC
	cluster['network_prefix'] = network_prefix = '10.0.0.0/16'

	print('Creating Virtual Private Cloud (VPC) with prefix ' + network_prefix + '...', end='')
	vpc = ec2.create_vpc(CidrBlock=network_prefix, InstanceTenancy='default', AmazonProvidedIpv6CidrBlock=False)
	vpc_id = vpc['Vpc']['VpcId']
	cluster['vpc_id'] = vpc_id
	_tag_cluster_res(ec2, cluster_name, vpc_id, 'VPC')
	

	print('configuring...', end='')
	ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={'Value':True})
	ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={'Value':True})
	print('done')

	### Creating internet gateway
	print('Creating Internet Gateway (IGW)...', end='')
	igw = ec2.create_internet_gateway()
	igw_id = igw['InternetGateway']['InternetGatewayId']
	cluster['igw_id'] = igw_id
	_tag_cluster_res(ec2, cluster_name, igw_id, 'IGW')
	print('attaching...', end='')
	ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
	print('done')

	### Fixing route table
	print('Fixing route table (RTB)...', end='')
	rtb = ec2.describe_route_tables(Filters=[{'Name':'vpc-id', 'Values':[vpc_id]}])['RouteTables']
	rtb_id = rtb[0]['RouteTableId']
	_tag_cluster_res(ec2, cluster_name, rtb_id, 'RTB')
	ec2.create_route(RouteTableId=rtb_id, DestinationCidrBlock='0.0.0.0/0', GatewayId=igw_id)
	cluster['rtb_id'] = rtb_id
	print('done')

	### Creating subnets
	print('Creating subnets...', end='')

	network = ipaddress.ip_network(unicode(network_prefix))
	prefixlen_diff = int(ceil(log2(len(availability_zones))))
	zone_subnets = [(zone, str(sn)) for zone, sn in zip(availability_zones, network.subnets(prefixlen_diff=prefixlen_diff))]
	
	cluster['subnets'] = subnets = []
	for zone, subnet in zone_subnets:
		print(subnet + '(' + zone + ')...', end='')
		res = ec2.create_subnet(VpcId=vpc_id, CidrBlock=subnet, AvailabilityZone=zone)
		ec2.modify_subnet_attribute(SubnetId=res['Subnet']['SubnetId'], MapPublicIpOnLaunch={'Value':True})
		subnets.append(res['Subnet'])

	subnet_ids = {subnet['AvailabilityZone']:(subnet['SubnetId'], subnet['CidrBlock']) for subnet in subnets}
	for az, (subnet_id, cidr) in subnet_ids.items():
		_tag_cluster_res(ec2, cluster_name, subnet_id, az + ' subnet')
	cluster['subnet_ids'] = subnet_ids
	print('done')

	### Creating security groups
	print('Creating security groups...', end='')
	sgcontroller_name = cluster_name + '_controller'
	sgengine_name = cluster_name + '_engine'	
	sgdata_name = cluster_name + '_data'

	cluster['sgdata'] = dict()
	cluster['sgdata']['name'] = sgdata_name
	cluster['sgengine'] = dict()
	cluster['sgengine']['name'] = sgengine_name
	cluster['sgcontroller'] = dict()
	cluster['sgcontroller']['name'] = sgcontroller_name

	print(sgcontroller_name + '...', end='')
	sgcontroller = ec2.create_security_group(GroupName=sgcontroller_name, Description=sgcontroller_name, VpcId=vpc_id)
	sgcontroller_id = sgcontroller['GroupId']
	cluster['sgcontroller']['id'] = sgcontroller_id
	_tag_cluster_res(ec2, cluster_name, sgcontroller_id, 'controller SG')

	print(sgengine_name + '...', end='')
	sgengine = ec2.create_security_group(GroupName=sgengine_name, Description=sgengine_name, VpcId=vpc_id)
	sgengine_id = sgengine['GroupId']
	cluster['sgengine']['id'] = sgengine_id
	_tag_cluster_res(ec2, cluster_name, sgengine_id, 'engine SG')
	
	print(sgdata_name + '...', end='')
	sgdata = ec2.create_security_group(GroupName=sgdata_name, Description=sgdata_name, VpcId=vpc_id)
	sgdata_id = sgdata['GroupId']
	cluster['sgdata']['id'] = sgdata_id
	_tag_cluster_res(ec2, cluster_name, sgdata_id, 'data SG')
	
	#cluster['security_groups'] = [(sgcontroller_name, sgcontroller_id), (sgengine_name, sgengine_id), (sgdata_name, sgdata_id)]
	cluster['sgengine_id'] = sgengine_id
	cluster['sgdata_id'] = sgdata_id
	cluster['sgcontroller_id'] = sgcontroller_id
	print('done')


	### Configuring security groups (seriously, boto3, a list of dict contraining lists of dicts? this is NOT pythonic...)
	print('Configuring security groups...', end='')

	print(sgcontroller_name + '...', end='')
	controller_fromengine_all = [{'IpProtocol':'-1', 'UserIdGroupPairs':[{'GroupId':sgengine_id, 'VpcId':vpc_id}]}]
	controller_fromdata_nfs = [{'IpProtocol':'tcp', 'FromPort':2049, 'ToPort':2049, 'UserIdGroupPairs':[{'GroupId':sgdata_id, 'VpcId':vpc_id}]}]
	controller_fromall_ssh = [{'IpProtocol':'tcp', 'FromPort':22, 'ToPort':22, 'IpRanges':[{'CidrIp':'0.0.0.0/0'}]}]
	ec2.authorize_security_group_ingress(GroupId=sgcontroller_id, IpPermissions=controller_fromengine_all)
	ec2.authorize_security_group_ingress(GroupId=sgcontroller_id, IpPermissions=controller_fromdata_nfs)
	ec2.authorize_security_group_ingress(GroupId=sgcontroller_id, IpPermissions=controller_fromall_ssh)
	cluster['sgcontroller']['IpPermissionsIngress'] = controller_fromengine_all + controller_fromdata_nfs + controller_fromall_ssh
	cluster['sgcontroller']['IpPermissionsEgress'] = []

	print(sgengine_name + '...', end='')
	engine_fromcontroller_all = [{'IpProtocol':'-1', 'UserIdGroupPairs':[{'GroupId':sgcontroller_id, 'VpcId':vpc_id}]}]
	engine_fromdata_nfs = [{'IpProtocol':'tcp', 'FromPort':2049, 'ToPort':2049, 'UserIdGroupPairs':[{'GroupId':sgdata_id, 'VpcId':vpc_id}]}]
	ec2.authorize_security_group_ingress(GroupId=sgengine_id, IpPermissions=engine_fromcontroller_all)
	ec2.authorize_security_group_ingress(GroupId=sgengine_id, IpPermissions=engine_fromdata_nfs)
	cluster['sgengine']['IpPermissionsIngress'] = engine_fromcontroller_all +  engine_fromdata_nfs
	cluster['sgengine']['IpPermissionsEgress'] = []

	print(sgdata_name + '...', end='')
	data_fromcontroller_nfs = [{'IpProtocol':'tcp', 'FromPort':2049, 'ToPort':2049, 'UserIdGroupPairs':[{'GroupId':sgcontroller_id, 'VpcId':vpc_id}]}]
	data_fromengine_nfs = [{'IpProtocol':'tcp', 'FromPort':2049, 'ToPort':2049, 'UserIdGroupPairs':[{'GroupId':sgengine_id, 'VpcId':vpc_id}]}]
	data_tocontroller_nfs = [{'IpProtocol':'tcp', 'FromPort':2049, 'ToPort':2049, 'UserIdGroupPairs':[{'GroupId':sgcontroller_id, 'VpcId':vpc_id}]}]
	data_toengine_nfs = [{'IpProtocol':'tcp', 'FromPort':2049, 'ToPort':2049, 'UserIdGroupPairs':[{'GroupId':sgengine_id, 'VpcId':vpc_id}]}]
	data_toall_revoke = [{'IpProtocol':'-1', 'IpRanges':[{'CidrIp':'0.0.0.0/0'}]}]
	ec2.authorize_security_group_ingress(GroupId=sgdata_id, IpPermissions=data_fromcontroller_nfs)
	ec2.authorize_security_group_ingress(GroupId=sgdata_id, IpPermissions=data_fromengine_nfs)
	ec2.authorize_security_group_egress(GroupId=sgdata_id, IpPermissions=data_tocontroller_nfs)
	ec2.authorize_security_group_egress(GroupId=sgdata_id, IpPermissions=data_toengine_nfs)
	ec2.revoke_security_group_egress(GroupId=sgdata_id, IpPermissions=data_toall_revoke)
	cluster['sgdata']['IpPermissionsIngress'] = data_fromcontroller_nfs + data_fromengine_nfs
	cluster['sgdata']['IpPermissionsEgress'] = data_tocontroller_nfs + data_toengine_nfs
	print('done')


	### Choosing controller+EBS AZ zone
	if controller_availability_zone is None:
		controller_availability_zone = choice(availability_zones)
		print('No controller+EBS Avail. Zone specified, so I chose ' + controller_availability_zone + ' for you.')
	else:
		print('Controller+EBS Avail. Zone: ' + controller_availability_zone)


	### Setting up EBS persistent data
	ebsdata_mount_point = '/ebsdata'
	ebsdata_device = '/dev/xvdd'
	volume_size = 16 # in GiB
	volume_type = 'gp2' # SSD
	# volume_type = 'io1' # SSD with provisioned iops (for critical io intensive task)
	# volume_type = 'standard' # Magnetic tape
	# volume_type = 'sc1' # EBS, low-cost HDD
	# volume_type = 'st1' # EBS, low-cost throughput optimized HDD

	print('Creating EBS data volume, (controller) ' + ebsdata_device + ' --> (controller) ' + ebsdata_mount_point 
		+ ' (' + str(volume_size) + ' GiB, ' + volume_type + ')...', end='')

	ebsdata_az = controller_availability_zone
	ebsdata = ec2.create_volume(Size=volume_size, AvailabilityZone=ebsdata_az, VolumeType=volume_type)
	ebsdata_id = ebsdata['VolumeId']
	cluster['ebsdata'] = dict()
	cluster['ebsdata']['volume_id'] = ebsdata_id
	cluster['ebsdata']['mount_point'] = ebsdata_mount_point
	cluster['ebsdata']['device'] = ebsdata_device
	_tag_cluster_res(ec2, cluster_name, ebsdata_id, 'EBS data')
	print('done')

	print('Creating key pair...', end='')
	key_name = '_'.join([cluster_name, cluster_region])
	existing_keypairs = ec2.describe_key_pairs()['KeyPairs']
	existing_keynames = set([k['KeyName'] for k in existing_keypairs])
	if key_name in existing_keynames:
		print(key_name + ' already exists and will be used (hope you kept that PEM file somewhere!)...', end='')
	else:
		print(key_name + ' --> ' + key_name + '.pem (read-only)...', end='')
		kp = ec2.create_key_pair(KeyName=key_name)
		with open(key_name + '.pem', 'w') as f:
			f.write(kp['KeyMaterial'])
		os.chmod(key_name + '.pem', 0400)
	print('done')

	cluster['keypair_name'] = key_name
	

	print('Launching controller instance...', end='')
	## We'll try and fetch the latest AMI for Amazon linux 
	## (different regions have different ID)
	amazon_simple_ami = ec2.describe_images(Filters=[{'Name':'name', 'Values':['amzn-ami-hvm*']}, 
													 {'Name':'block-device-mapping.volume-type', 'Values':['gp2']}])['Images']
	ami_linux_id = sorted(amazon_simple_ami, key=lambda x: x['Description'], reverse=True)[0]['ImageId']
	
	controller_instance_type = 't2.micro'
	controller_subnet_id = subnet_ids[controller_availability_zone][0]

	### Generating controller start-up script. This will only run once following instance creation
	with open('ipcontroller_config.sh', 'r') as f:
		controller_startup_script = f.read()
	controller_startup_script = controller_startup_script.format(ebsdata_device=ebsdata_device, 
																 ebsdata_mount_point=ebsdata_mount_point, 
																 network_prefix=network_prefix)
	#controller_startup_script_base64 = base64.b64encode(controller_startup_script)


	controller_instance = ec2.run_instances(ImageId=ami_linux_id, KeyName=key_name, 
											MinCount=1, MaxCount=1,
											InstanceType=controller_instance_type,
											Monitoring={'Enabled':False},
											UserData=controller_startup_script,
											NetworkInterfaces=[{'DeviceIndex':0, 
																'DeleteOnTermination':True, 
																'AssociatePublicIpAddress':True,
																'Groups':[sgcontroller_id],
																'SubnetId':controller_subnet_id}])
	controller_instance_id = controller_instance['Instances'][0]['InstanceId']
	cluster['controller_instance_id'] = controller_instance_id

	### Waiting for controller instance. Print ssh command.
	for t in count():
		description = ec2.describe_instances(InstanceIds=[controller_instance_id])['Reservations'][0]['Instances'][0]
		state = description['State']
		if state['Code'] == 16:
			controller_private_ip = description['PrivateIpAddress']
			controller_public_ip = description['PublicIpAddress']
			print(str(state['Code']) + ':' + state['Name'] + '!')
			break
		else:
			print(str(state['Code']) + ':' + state['Name'] + '...', end='')
		time.sleep(8)
		if t == 8:
			print('someone\'s slow!...', end='')
	print('Controller private IP: ' + controller_private_ip)
	print('Controller public IP: ' + controller_public_ip)
	key_path = os.getcwd() + '/' + key_name + '.pem'
	print('try this in a minute:\n\tssh -i ' + key_path + ' ec2-user@' + controller_public_ip)
	cluster['controller_private_ip'] = controller_private_ip
	### Attaching EBS data volume once the controller is in the running state
	print('Attaching EBS data volume to controller...', end='')
	ec2.attach_volume(VolumeId=ebsdata_id, InstanceId=controller_instance_id, Device=ebsdata_device)
	print('done')

	with open(cluster_name + '_ClusterResources.json', 'w') as f:
		json.dump(cluster, f, indent=1)

	print('Requesting spot fleet...')
	hist = ec2.describe_spot_price_history(StartTime=datetime.utcnow() - timedelta(hours=6), 
								EndTime=datetime.utcnow(), 
								InstanceTypes=['c4.large'],
								AvailabilityZone='ca-central-1b',
								Filters=[{'Name':'product-description', 'Values':['Linux/UNIX']}])
	spot_prices = [float(spot['SpotPrice']) for spot in hist['SpotPriceHistory']]
	mean_spot, median_spot, std_spot = mean(spot_prices), median(spot_prices), std(spot_prices)

	print('Spot price  $' + str(median_spot)*core_fleet_size + '/hour')


	print('-'*60)
	print('To access the ipyparallel cluster, first fetch the configuration file:')
	print('\tscp -i ' + key_path + ' ec2-user@' + controller_public_ip + ':' + ebsdata_mount_point + '/profile_ec2/security/ipcontroller-client.json .')
	print('and then from python:')
	print('\tfrom ipyparallel import Client')
	print('\tc = Client(\'ipcontroller-client.json\', sshserver=\'ec2-user@' + controller_public_ip + '\', sshkey=\'' + key_path + '\')')
	print('\tlbv = c.load_balanced_view()')
	print('\tlbv.queue_status()')


	return cluster


def dismantle_cluster(resources_file_or_dict, keep_EBSdata=True):
	if type(resources_file_or_dict) == str:
		with open(resources_file_or_dict, 'r') as f:
			cluster = json.load(f)
	elif (type(resources_file_or_dict) == dict):
		if not 'name' in resources_file_or_dict:
			raise Exception('Passed dictionary doesn\'t look like anything to me.')
		cluster = resources_file_or_dict
	else:
		raise Exception(resources_file_or_dict + ' doesn\'t look like anything to me.')

	ec2 = boto3.client('ec2', region_name=cluster['region'])

	print('Terminating controller instance...', end='')
	ec2.terminate_instances(InstanceIds=[cluster['controller_instance_id']])
	for t in count():
		try:
			state = ec2.describe_instances(InstanceIds=[cluster['controller_instance_id']])['Reservations'][0]['Instances'][0]['State']
			if state['Code'] == 48:
				print(str(state['Code']) + ':' + state['Name'] + '!')
				break
			else:
				print(str(state['Code']) + ':' + state['Name'] + '...', end='')
			time.sleep(8)
			if t == 8:
				print('someone\'s slow!...', end='')
		except:
			break

	if not keep_EBSdata:
		print('Deleting EBS data volume...', end='')
		try:
			ec2.delete_volume(VolumeId=cluster['ebsdata']['volume_id'])
		except Exception as e:
			if 'NotFound' in str(e):
				print('(NotFound)...', end='')
			else:
				print('\n' + str(e))

		print('done')

	sgs = ['sgdata', 'sgengine', 'sgcontroller']
	### Wiping security groups
	print('Wiping security group permissions...', end='')
	for sg in sgs:
		#data = ec2.describe_security_groups(Filters=[{'Name':'vpc-id', 'Values':[cluster['vpc_id']]}, {'Name':'group-id', 'Values':[cluster[sgn]]}])
		print(cluster[sg]['name'] + '...', end='')
		try:
			if cluster[sg]['IpPermissionsIngress'] != []:
				ec2.revoke_security_group_ingress(GroupId=cluster[sg]['id'], IpPermissions=cluster[sg]['IpPermissionsIngress'])
		except Exception as e:
			if 'NotFound' in str(e):
				print('(NotFound)...', end='')
			else:
				print('\n' + str(e))

		try:
			if cluster[sg]['IpPermissionsEgress'] != []:
				ec2.revoke_security_group_egress(GroupId=cluster[sg]['id'], IpPermissions=cluster[sg]['IpPermissionsEgress'])
		except Exception as e:
			if 'NotFound' in str(e):
				print('(NotFound)...', end='')
			else:
				print('\n' + str(e))
	print('done')

	### Deleting security groups
	print('Deleting security groups...', end='')
	for sg in sgs:
		print(cluster[sg]['name'] + '...', end='')
		try:
			ec2.delete_security_group(GroupId=cluster[sg]['id'])
		except Exception as e:
			if 'NotFound' in str(e):
				print('(NotFound)...', end='')
			else:
				print('\n' + str(e))
	print('done')

	### Deleting subnets
	print('Deleting subnets...', end='')
	# for zone, (subnet_id, _) in subnet_ids.items():
	# 	print(zone + '(' + subnet_id + ')..', end='')
	# 	try:
	# 		ec2.delete_subnet(SubnetId=subnet_id)
	# 	except Exception as e:
	# 		print('\n' + str(e))
	for subnet in ec2.describe_subnets(Filters=[{'Name':'vpc-id', 'Values':[cluster['vpc_id']]}])['Subnets']:
		print(subnet['CidrBlock'] + '(' + subnet['AvailabilityZone'] + ')...', end='')
		try:
			ec2.delete_subnet(SubnetId=subnet['SubnetId'])
		except Exception as e:
			if 'NotFound' in str(e):
				print('(NotFound)...', end='')
			else:
				print('\n' + str(e))
	print('done')

	print('Deleting internet route...', end='')
	# rtb_id = ec2.describe_route_tables(Filters=[{'Name':'vpc-id', 'Values':[vpc_id]}])['RouteTables'][0]['RouteTableId']
	try:
		ec2.delete_route(RouteTableId=cluster['rtb_id'], DestinationCidrBlock='0.0.0.0/0')
	except Exception as e:
			if 'NotFound' in str(e):
				print('(NotFound)...', end='')
			else:
				print('\n' + str(e))
	print('done')

	
	print('Detaching internet gateway...', end='')
	try:
		ec2.detach_internet_gateway(InternetGatewayId=cluster['igw_id'], VpcId=cluster['vpc_id'])
	except Exception as e:
			if 'NotFound' in str(e):
				print('(NotFound)...', end='')
			else:
				print('\n' + str(e))
	print('done')


	# print('Deleting route table...', end='')
	# try:
	# 	ec2.delete_route_table(RouteTableId=cluster['rtb_id'])
	# except Exception as e:
	# 	print('\n' + str(e))
	# print('done')

	### Weirdly enough deleting the internet gateway
	### deletes the route table alright, but not its tags
	try:
		ec2.delete_tags(Resources=[cluster['rtb_id']])
	except:
		pass


	print('Deleting internet gateway...', end='')
	try:
		ec2.delete_internet_gateway(InternetGatewayId=cluster['igw_id'])
	except Exception as e:
		if 'NotFound' in str(e):
			print('(NotFound)...', end='')
		else:
			print('\n' + str(e))
	print('done')

	### Deleting VPC
	print('Deleting Virtual Private Cloud...', end='')
	try:
		ec2.delete_vpc(VpcId=cluster['vpc_id'])
	except Exception as e:
		if 'NotFound' in str(e):
			print('(NotFound)...', end='')
		else:
			print('\n' + str(e))
	print('done')

	print('Cluster ' + cluster['name'] + ' dismantled!')



def main():
	pass

if __name__ == '__main__':
	main()
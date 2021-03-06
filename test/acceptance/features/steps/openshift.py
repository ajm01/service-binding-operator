import re
import time
from pyshould import should
from command import Command

nodejs_app = "https://github.com/pmacik/nodejs-rest-http-crud"


class Openshift(object):
    def __init__(self):
        self.cmd = Command()
        self.catalog_source_yaml_template = '''
apiVersion: operators.coreos.com/v1alpha1
kind: CatalogSource
metadata:
    name: {name}
    namespace: openshift-marketplace
spec:
    sourceType: grpc
    image: {catalog_image}
    displayName: {name} OLM registry
    updateStrategy:
        registryPoll:
            interval: 30m
'''
        self.operator_subscription_yaml_template = '''
---
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
    name: '{name}'
    namespace: openshift-operators
spec:
    channel: '{channel}' # the quotes are necessary to avoid conversions from strings like '1.0' to be converted to actual decimal numbers
    installPlanApproval: Automatic
    name: '{name}'
    source: '{operator_source_name}'
    sourceNamespace: openshift-marketplace
    startingCSV: '{csv_version}'
'''

    def get_pod_lst(self, namespace):
        return self.get_resource_lst("pods", namespace)

    def get_resource_lst(self, resource_plural, namespace):
        (output, exit_code) = self.cmd.run(f'oc get {resource_plural} -n {namespace} -o "jsonpath={{.items[*].metadata.name}}"')
        exit_code | should.be_equal_to(0)
        return output

    def search_item_in_lst(self, lst, search_pattern):
        lst_arr = lst.split(" ")
        for item in lst_arr:
            if re.fullmatch(search_pattern, item) is not None:
                print(f"item matched {item}")
                return item
        print("Given item not matched from the list of pods")
        return None

    def search_pod_in_namespace(self, pod_name_pattern, namespace):
        return self.search_resource_in_namespace("pods", pod_name_pattern, namespace)

    def search_resource_in_namespace(self, resource_plural, name_pattern, namespace):
        print(f"Searching for {resource_plural} that matches {name_pattern} in {namespace} namespace")
        lst = self.get_resource_lst(resource_plural, namespace)
        if len(lst) != 0:
            print("Resource list is {}".format(lst))
            return self.search_item_in_lst(lst, name_pattern)
        else:
            print('Resource list is empty under namespace - {}'.format(namespace))
            return None

    def is_resource_in(self, resource_type):
        output, exit_code = self.cmd.run(f'oc get {resource_type}')
        return exit_code == 0

    def wait_for_pod(self, pod_name_pattern, namespace, interval=5, timeout=60):
        pod = self.search_pod_in_namespace(pod_name_pattern, namespace)
        start = 0
        if pod is not None:
            return pod
        else:
            while ((start + interval) <= timeout):
                pod = self.search_pod_in_namespace(pod_name_pattern, namespace)
                if pod is not None:
                    return pod
                time.sleep(interval)
                start += interval
        return None

    def check_pod_status(self, pod_name, namespace, wait_for_status="Running"):
        cmd = f'oc get pod {pod_name} -n {namespace} -o "jsonpath={{.status.phase}}"'
        status_found, output, exit_status = self.cmd.run_wait_for_status(cmd, wait_for_status)
        return status_found

    def get_pod_status(self, pod_name, namespace):
        cmd = f'oc get pod {pod_name} -n {namespace} -o "jsonpath={{.status.phase}}"'
        output, exit_status = self.cmd.run(cmd)
        print(f"Get pod status: {output}, {exit_status}")
        if exit_status == 0:
            return output
        return None

    def oc_apply(self, yaml):
        (output, exit_code) = self.cmd.run("oc apply -f -", yaml)
        return output

    def create_catalog_source(self, name, catalog_image):
        catalog_source = self.catalog_source_yaml_template.format(name=name, catalog_image=catalog_image)
        return self.oc_apply(catalog_source)

    def get_current_csv(self, package_name, catalog, channel):
        cmd = f'oc get packagemanifests -o json | jq -r \'.items[] \
            | select(.metadata.name=="{package_name}") \
            | select(.status.catalogSource=="{catalog}").status.channels[] \
            | select(.name=="{channel}").currentCSV\''
        current_csv, exit_code = self.cmd.run(cmd)

        if current_csv is None:
            return current_csv

        current_csv = current_csv.strip("\n")
        if current_csv == "" or exit_code != 0:
            current_csv = None
        return current_csv

    def create_operator_subscription(self, package_name, operator_source_name, channel):
        operator_subscription = self.operator_subscription_yaml_template.format(
            name=package_name, operator_source_name=operator_source_name,
            channel=channel, csv_version=self.get_current_csv(package_name, operator_source_name, channel))
        return self.oc_apply(operator_subscription)

    def wait_for_package_manifest(self, package_name, operator_source_name, operator_channel, interval=5, timeout=120):
        current_csv = self.get_current_csv(package_name, operator_source_name, operator_channel)
        start = 0
        if current_csv is not None:
            return True
        else:
            while ((start + interval) <= timeout):
                current_csv = self.get_current_csv(package_name, operator_source_name, operator_channel)
                if current_csv is not None:
                    return True
                time.sleep(interval)
                start += interval
        return False

    def expose_service_route(self, service_name, namespace):
        output, exit_code = self.cmd.run(f'oc expose svc/{service_name} -n {namespace} --name={service_name}')
        return re.search(r'.*%s\sexposed' % service_name, output)

    def get_route_host(self, name, namespace):
        (output, exit_code) = self.cmd.run(f'oc get route {name} -n {namespace} -o "jsonpath={{.status.ingress[0].host}}"')
        exit_code | should.be_equal_to(0)
        return output

    def check_for_deployment_status(self, deployment_name, namespace, wait_for_status="True"):
        deployment_status_cmd = f'oc get deployment {deployment_name} -n {namespace} -o "jsonpath={{.status.conditions[*].status}}"'
        deployment_status, exit_code = self.cmd.run_wait_for_status(deployment_status_cmd, wait_for_status, 5, 300)
        exit_code | should.be_equal_to(0)
        return deployment_status

    def get_deployment_env_info(self, name, namespace):
        env_cmd = f'oc get deploy {name} -n {namespace} -o "jsonpath={{.spec.template.spec.containers[0].env}}"'
        env, exit_code = self.cmd.run(env_cmd)
        exit_code | should.be_equal_to(0)
        return env

    def get_deployment_envFrom_info(self, name, namespace):
        env_from_cmd = f'oc get deploy {name} -n {namespace} -o "jsonpath={{.spec.template.spec.containers[0].envFrom}}"'
        env_from, exit_code = self.cmd.run(env_from_cmd)
        exit_code | should.be_equal_to(0)
        return env_from

    def get_resource_info_by_jsonpath(self, resource_type, name, namespace, json_path, wait=False):
        output, exit_code = self.cmd.run(f'oc get {resource_type} {name} -n {namespace} -o "jsonpath={json_path}"')
        if exit_code != 0:
            if wait:
                attempts = 5
                while exit_code != 0 and attempts > 0:
                    output, exit_code = self.cmd.run(f'oc get {resource_type} {name} -n {namespace} -o "jsonpath={json_path}"')
                    attempts -= 1
                    time.sleep(5)
        exit_code | should.be_equal_to(0).desc(f'Exit code should be 0:\n OUTPUT:\n{output}')
        return output

    def get_resource_info_by_jq(self, resource_type, name, namespace, jq_expression, wait=False):
        output, exit_code = self.cmd.run(f'oc get {resource_type} {name} -n {namespace} -o json | jq -rc \'{jq_expression}\'')
        if exit_code != 0:
            if wait:
                attempts = 5
                while exit_code != 0 and attempts > 0:
                    output, exit_code = self.cmd.run(f'oc get {resource_type} {name} -n {namespace} -o json | jq -rc \'{jq_expression}\'')
                    attempts -= 1
                    time.sleep(5)
        exit_code | should.be_equal_to(0).desc(f'Exit code should be 0:\n OUTPUT:\n{output}')
        return output.rstrip("\n")

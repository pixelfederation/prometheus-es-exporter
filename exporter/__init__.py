import argparse
import configparser
import json
import sched
import time

from elasticsearch import Elasticsearch
from prometheus_client import start_http_server, Gauge

from exporter.parser import parse_response

gauges = {}

def format_label_value(value_list):
    return '_'.join(value_list)

def format_metric_name(name_list):
    return '_'.join(name_list)

def update_gauges(metrics):
    metric_dict = {}
    for (name_list, label_dict, value) in metrics:
        metric_name = format_metric_name(name_list)
        if metric_name not in metric_dict:
            metric_dict[metric_name] = (tuple(label_dict.keys()), {})

        label_keys = metric_dict[metric_name][0]
        label_values = tuple([
            format_label_value(label_dict[key])
            for key in label_keys
        ])

        metric_dict[metric_name][1][label_values] = value

    for metric_name, (label_keys, value_dict) in metric_dict.items():
        if metric_name in gauges:
            (old_label_values_set, gauge) = gauges[metric_name]
        else:
            old_label_values_set = set()
            gauge = Gauge(metric_name, '', label_keys)

        new_label_values_set = set(value_dict.keys())

        for label_values in old_label_values_set - new_label_values_set:
            gauge.remove(*label_values)

        for label_values, value in value_dict.items():
            if label_values:
                gauge.labels(*label_values).set(value)
            else:
                gauge.set(value)

        gauges[metric_name] = (new_label_values_set, gauge)

def run_scheduler(scheduler, es_client, name, interval, query):
    def scheduled_run(scheduled_time, interval):
        try:
            response = es_client.search(body=query)
        except Exception as ex:
            pass
        else:
            metrics = parse_response(response, [name])
            update_gauges(metrics)

        next_scheduled_time = scheduled_time + interval
        scheduler.enterabs(
            next_scheduled_time,
            1,
            scheduled_run,
            (next_scheduled_time, interval)
        )

    next_scheduled_time = time.monotonic()
    scheduler.enterabs(
        next_scheduled_time,
        1,
        scheduled_run,
        (next_scheduled_time, interval)
    )

def main():
    parser = argparse.ArgumentParser(description='Export ES query results to Prometheus.')
    parser.add_argument('-e', '--es-cluster', default='localhost',
        help='addresses of nodes in a Elasticsearch cluster to run queries on. Nodes should be separated by commas e.g. es1,es2. Ports can be provided if non-standard (9200) e.g. es1:9999 (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=8080,
        help='port to serve the metrics endpoint on. (default: 8080)')
    parser.add_argument('-c', '--config-file', default='exporter.cfg',
        help='path to query config file. Can be absolute, or relative to the current working directory. (default: exporter.cfg)')
    args = parser.parse_args()

    port = args.port
    es_cluster = args.es_cluster.split(',')

    config = configparser.ConfigParser()
    config.read(args.config_file)

    query_prefix = 'query_'
    queries = {}
    for section in config.sections():
        if section.startswith(query_prefix):
            query_name = section[len(query_prefix):]
            query_interval = config.getfloat(section, 'QueryIntervalSecs')
            query = json.loads(config.get(section, 'QueryJson'))

            queries[query_name] = (query_interval, query)

    es_client = Elasticsearch(es_cluster)

    scheduler = sched.scheduler()

    print('Starting server...')
    start_http_server(port)
    print('Server started on port {}'.format(port))

    for name, (interval, query) in queries.items():
        run_scheduler(scheduler, es_client, name, interval, query)

    try:
        scheduler.run()
    except KeyboardInterrupt:
        pass

    print('Shutting down')
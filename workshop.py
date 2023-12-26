#!/usr/bin/python3

import sys
import getopt
import os
import urllib.request
import urllib.parse
from urllib.error import HTTPError, URLError
import json
import time
import threading

# Downloading a large collection at once on a new install of L4D2 may cause errors on bootup.
# Set this if you'd like to cap the amount of downloads at once. (Undownloaded plugins will resume
# next time the script launches).  0 = don't limit downloads.
g_iLimitDownloads = 0

def safe_print(*objects, errors='ignore', **kwargs):
    '''
    An ascii-only print function to avoid encoding issues.
    '''
    print(*(str(t).encode('ascii', errors=errors).decode('ascii') for t in objects), **kwargs)

def usage(cmd, exit):
    print("usage: " + cmd + " [-o <output_dir>] [<collection_id>]..." \
          " <collection_id>")
    sys.exit(exit)

const_urls = {
    'file': "http://api.steampowered.com/ISteamRemoteStorage/" \
            "GetPublishedFileDetails/v1",
    'collection': "http://api.steampowered.com/ISteamRemoteStorage/" \
                  "GetCollectionDetails/v0001"
}

const_data = {
    'file': {'itemcount': 0, 'publishedfileids[0]': 0},
    'collection': {'collectioncount': 0, 'publishedfileids[0]': 0}
}

download_lock = threading.Lock()

def download_plugins_concurrently(output_dir, plugins, old_plugins):
    fail = []
    succeed = {}
    error = 0
    downloads = 0

    def download_plugin(plugin):
        nonlocal error, downloads
        if 'file_url' in plugin:
            plugin_display_name = '"{title}" ({publishedfileid}.vpk)'.format(**plugin)
            if plugin['publishedfileid'] in old_plugins and \
                    old_plugins[plugin['publishedfileid']]['time_updated'] == \
                    plugin['time_updated']:
                safe_print("Plugin " + plugin_display_name + " already up-to-date")
                succeed[plugin['publishedfileid']] = {k: plugin[k] for k in ('title', 'time_updated') if k in plugin}
            else:
                try:
                    name = plugin['publishedfileid'] + ".vpk"
                    safe_print("Downloading " + plugin_display_name)
                    path = os.path.join(output_dir, name)
                    urllib.request.urlretrieve(plugin['file_url'], path)
                    print("Downloading complete")
                    succeed[plugin['publishedfileid']] = {k: plugin[k] for k in ('title', 'time_updated') if k in plugin}
                    downloads += 1
                    if downloads == g_iLimitDownloads:
                        print("Finished downloading limited map pool ({}/{} plugins downloaded)".format(downloads, downloads))

                    time.sleep(10)

                except HTTPError as e:
                    with download_lock:
                        error += 1
                        fail.append(plugin)
                    safe_print("Server returned " + str(e.code) + " error on " + plugin_display_name)

    threads = []
    for plugin in plugins:
        if downloads >= g_iLimitDownloads and g_iLimitDownloads != 0:
            continue
        thread = threading.Thread(target=download_plugin, args=(plugin,))
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()

    return error, fail, succeed

def init(argv):
    error = 0
    output_dir = os.getcwd()
    collections_id_list = []
    save_file = os.path.join(output_dir, "addons.lst")
    if len(argv) == 1 and not os.path.isfile(save_file):
        print("No save file found")
        usage(argv[0], 0)
    try:
        opts, args = getopt.getopt(argv[1:], "ho:")
    except getopt.GetoptError:
        usage(argv[0], 2)
    else:
        for opt, arg in opts:
            if opt == 'h':
                usage(argv[0], 0)
            elif opt == '-o':
                output_dir = os.path.abspath(arg)
                save_file = os.path.join(output_dir, "addons.lst")
        if not os.path.exists(output_dir):
            print(output_dir + ": path doesn't exist\nEnd of program")
            error += 1
        collections_id_list = argv[len(opts) * 2 + 1:]
    return error, output_dir, collections_id_list, save_file

def load_saved_data(save_file):
    if os.path.isfile(save_file):
        with open(save_file, 'r') as file:
            saved_data = json.load(file)
    else:
        saved_data = {}
    return saved_data

def get_plugins_id_from_collections_list(collections_id_list):
    valid_collections = []
    sub_collection = []
    plugins_id_list = []
    error = None
    data = const_data['collection']
    data['collectioncount'] = len(collections_id_list)
    for idx, collection_id in enumerate(collections_id_list):
        data['publishedfileids[' + str(idx) + ']'] = collection_id
    encode_data = urllib.parse.urlencode(data).encode('ascii')
    try:
        response = urllib.request.urlopen(const_urls['collection'], encode_data, timeout=10)
    except HTTPError as e:
        print("Server returned " + str(e.code) + " error")
        error = e
    except URLError as e:
        print("Can't reach server: " + e.reason)
        error = e
    else:
        json_response = json.loads(response.read().decode('utf-8'))
        for collection in json_response['response']['collectiondetails']:
            if 'children' in collection:
                valid_collections.append(collection['publishedfileid'])
                for item in collection['children']:
                    if item['filetype'] == 0:
                        plugins_id_list.append(item['publishedfileid'])
                    elif item['filetype'] == 2:
                        sub_collection.append(item['publishedfileid'])
                    else:
                        print("Unrecognized filetype: " + str(item['filetype']))
        if sub_collection:
            error, plugins_id_list_temp, _ = get_plugins_id_from_collections_list(sub_collection)
            if error is None:
                plugins_id_list += plugins_id_list_temp
    return error, plugins_id_list, valid_collections

def get_plugins_info(plugins_id_list):
    plugin_info = []
    error = None
    data = const_data['file']
    data['itemcount'] = len(plugins_id_list)
    for idx, plugin_id in enumerate(plugins_id_list):
        data['publishedfileids[' + str(idx) + ']'] = plugin_id
    encode_data = urllib.parse.urlencode(data).encode('ascii')
    try:
        response = urllib.request.urlopen(const_urls['file'], encode_data, timeout=10)
    except HTTPError as e:
        print("Server returned " + str(e.code) + " error")
        error = e
    except URLError as e:
        print("Can't reach server: " + e.reason)
        error = e
    else:
        json_response = json.loads(response.read().decode('utf-8'))
        for plugin in json_response['response']['publishedfiledetails']:
            plugin_info.append(plugin)
    return error, plugin_info

def plugins_to_remove(plugins_id_list, old_plugins):
    # Initialize a list to store deprecated plugins
    deprecated_plugins = []

    # Iterate through the keys (plugin IDs) in old_plugins
    for plugin_id in old_plugins.keys():
        # Check if the plugin ID is not in the plugins_id_list
        if plugin_id not in plugins_id_list:
            # If it's not in the list, it's deprecated, so add it to the deprecated_plugins list
            deprecated_plugins.append(plugin_id)

    # Return the list of deprecated plugins
    return deprecated_plugins


def main(argv):
    sleep = 15
    error, output_dir, collections_id_list, save_file = init(argv)
    if error == 0:
        saved_data = load_saved_data(save_file)
        if 'collections' in saved_data:
            if not collections_id_list:
                collections_id_list = saved_data['collections']
            else:
                collections_id_list += saved_data['collections']
                collections_id_list = list(set(collections_id_list))
        if not collections_id_list:
            print("No collection(s) ID given and no collection(s) ID found in " + save_file)
            error = 1
    if error == 0:
        error, plugins_id_list, valid_collections = get_plugins_id_from_collections_list(collections_id_list)
    if error is None:
        saved_data['collections'] = valid_collections
        if 'plugins' in saved_data:
            old_plugins = saved_data['plugins']
            deprecated_plugins = plugins_to_remove(plugins_id_list, old_plugins)
            deprecated_plugins = list(set(deprecated_plugins))
            if deprecated_plugins:
                error, deprecated_plugin_info = get_plugins_info(deprecated_plugins)
                if error is None:
                    print("\nSome plugins found which are no longer in workshop collection(s).")
                    print("Removing deprecated plugins:\n")
                    print_deprecated_info(deprecated_plugin_info)
                    saved_data, old_plugins = deletePlugins(deprecated_plugins, output_dir, saved_data, old_plugins)
            plugins_id_list += old_plugins.keys()
            plugins_id_list = list(set(plugins_id_list))
        else:
            old_plugins = {}
        saved_data['plugins'] = {}
        error, plugins_info = get_plugins_info(plugins_id_list)
    if error is None:
        num_download_failures = 0
        print("\n")
        while plugins_info and num_download_failures < 5:
            error, plugins_info, succeed_temp = download_plugins_concurrently(output_dir, plugins_info, old_plugins)
            saved_data['plugins'].update(succeed_temp)
            with open(save_file, 'w') as file:
                json.dump(saved_data, file, indent=4)
            if error > 0:
                print(f"{len(plugins_info)} plugins failed to download, retrying in {sleep} seconds")
                time.sleep(sleep)
                num_download_failures += 1
                print('--------------------------------------------------')
                print(f'Failed downloads (attempt #{num_download_failures} / 5)')
            else:
                num_download_failures = 0
        if num_download_failures:
            print('Gave up on downloading all plugins, blame Valve')
        else:
            print('Downloaded all plugins successfully')

if __name__ == "__main__":
    main(sys.argv)


import requests
import json
import os
import sys
import logging
import os

logging.basicConfig()
logger = logging.getLogger(__name__)

# __all__ = ["search", "download", "check", "get_auth_headers"]

API_URL = "https://astroarchive.noirlab.edu/api"
SEARCH_URL = API_URL + "/adv_search/find/"
HEADER_URL = API_URL + "/header/{md5}/"
CHECK_URL = API_URL + "/check/{md5}/"
RETRIEVE_URL = API_URL + "/retrieve/{md5}/"

HEADERS = {
    "accept": "application/json",
    "Content-Type": "application/json"
}
RETRIES = 3

session = requests.Session()
# retry = Retry(connect=RETRIES, backoff_factor=0.5)
# adapter = HTTPAdapter(max_retries=retry, pool_maxsize=128)
# session.mount('http://', adapter)
# session.mount('https://', adapter)

def _search(query={}, limit=100, offset=0, rectype="file", count="N"):
    params = {
        "limit": str(limit),
        "offset": str(offset),
        "rectype": rectype,
        "count": count,
    }

    logger.debug(f"Sending API request with query: {query} and params: {params}")

    r = requests.Request("POST", SEARCH_URL, params=params, data=json.dumps(query), headers=HEADERS)
    r = r.prepare()
    
    # s = requests.Session()
    response = session.send(r)
    response.raise_for_status()
    logger.debug(f"Got response content: {response.content}" )
    result = response.json()
    logger.debug(f"Got response json: {json.dumps(result)}")
    meta = result[0]
    data = result[1:]
    return meta, data

def search_fasearch(query={}, limit=100, rectype="file"):
    more = True
    results = []
    offset = 0
    while more:
        meta, _results = _search(query=query, limit=limit, offset=offset, rectype=rectype)
        more = meta['RESULTS']['MORE']
        results += _results
        offset += limit
    return results

def search(query={}, first=None, limit=1000, rectype="file"):
    import math
    meta, results = _search(query=query, rectype=rectype, count="Y")
    
    count = int(results[0]['count'])
    logger.debug(f"Found {count} results for query.")
    if first is not None:
        if first < count:
            logger.debug(f"Limiting to {first} results.")
            count = first
        if first < limit:
            limit = first

    num_queries = math.ceil(count / limit)
    logger.debug(f"Will make {num_queries} queries to get {count} results.")
    query_offsets = [i * limit for i in range(num_queries)]
    results = []
    for query_offset in query_offsets:
        meta, _results = _search(query=query, rectype=rectype, offset=query_offset, limit=limit)
        types = meta['HEADER']
        for r in _results:
            d = {}
            for k, v in r.items():
                if types[k] == 'np.float64':
                    if v is None or v == "None":
                        d[k] = float("NaN")
                    else:
                        d[k] = float(v)
                else:
                    d[k] = v
            results.append(d)
    
    return results

def get_auth_headers():
    email = os.environ.get("NOIRLAB_USER", None)
    password = os.environ.get("NOIRLAB_PASS", None)

    credentials_file = os.path.join(os.environ.get("PROC_DECAM_DIR"), "etc/noirlab.credentials")
    if os.path.exists(credentials_file):
        with open(credentials_file, "r") as f:
            credentials = f.read().strip()
            email, password = credentials.split(" ")
    if email is not None and password is not None:
        r = requests.Request("POST", API_URL + "/get_token/", data=json.dumps(dict(email=email, password=password)), headers=HEADERS)
        r = r.prepare()
        
        # s = requests.Session()
        response = session.send(r)
        logger.debug(f"Got response content: {response.content}" )
        response.raise_for_status()
        result = response.json()
        logger.debug(f"Got response json: {json.dumps(result)}")
        auth_token = result

        return {"Authorization": auth_token}
    
    return {}

def download(md5, progress=True, headers={}):
    url = RETRIEVE_URL.format(md5=md5)

    logger.debug(f"sending GET to {url} with headers {headers}")
    r = requests.Request("GET", url, headers=headers)
    r = r.prepare()
    # s = requests.Session()
    with session.send(r, stream=True) as response:
        total_length = response.headers.get('Content-Length')
        if progress:
            dl = 0
            total_length = int(total_length)
            for chunk in response.iter_content(chunk_size=1024*1024): 
                dl += len(chunk)
                done = int(50 * dl / total_length)
                sys.stdout.write("\r[%s%s (%s/%sMB)]" % ('=' * done, ' ' * (50-done), int(dl / (1024**2)), int(total_length / (1024**2))) )    
                sys.stdout.flush()
                yield chunk
            if dl >= total_length:
                sys.stdout.write("\n")
                sys.stdout.flush()
        else:
            for chunk in response.iter_content(chunk_size=1024*1024): 
                yield chunk

def check(md5, headers={}):
    url = CHECK_URL.format(md5=md5)
    r = requests.Request("GET", url, headers=headers)
    r = r.prepare()
    response = session.send(r)
    response.raise_for_status()

    logger.debug(f"Got response content: {response.content}" )
    result = response.json()
    return result['valid']


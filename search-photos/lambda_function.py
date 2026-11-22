import json, os, re, uuid, urllib.request, urllib.parse
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

REGION = os.environ.get("AWS_REGION", "us-east-1")
ES = os.environ["ES_ENDPOINT"].rstrip("/")
if not ES.startswith("http"):
    ES = "https://" + ES
BOT_ID = os.environ.get("LEX_BOT_ID", "")
ALIAS_ID = os.environ.get("LEX_BOT_ALIAS_ID", "TSTALIASID")
LOCALE = os.environ.get("LEX_LOCALE", "en_US")
lex = boto3.client("lexv2-runtime")
STOP = {"show", "me", "photos", "photo", "pictures", "picture", "images", "image",
        "with", "in", "them", "of", "and", "the", "a", "an", "find", "search",
        "for", "that", "have", "has", "containing", "please", "pics", "pic", "all"}
CORS = {"Access-Control-Allow-Origin": "*", "Content-Type": "application/json"}


def singular(w):
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def lex_keywords(q):
    if not BOT_ID:
        return []
    try:
        r = lex.recognize_text(botId=BOT_ID, botAliasId=ALIAS_ID, localeId=LOCALE,
                               sessionId=uuid.uuid4().hex, text=q)
    except Exception as e:
        print("Lex error", e)
        return []
    kws = []
    for intent in [r.get("sessionState", {}).get("intent", {})]:
        for slot in (intent.get("slots") or {}).values():
            if slot and slot.get("value"):
                v = slot["value"].get("interpretedValue") or slot["value"].get("originalValue")
                if v:
                    kws.append(v)
    return kws


def fallback_keywords(q):
    return [w for w in re.findall(r"[a-z0-9]+", q.lower()) if w not in STOP]


def es_search(terms):
    should = []
    for t in terms:
        should.append({"match": {"labels": t}})
        s = singular(t)
        if s != t:
            should.append({"match": {"labels": s}})
    body = {"size": 50, "query": {"bool": {"should": should, "minimum_should_match": 1}}}
    url = ES + "/photos/_search"
    data = json.dumps(body)
    req = AWSRequest(method="POST", url=url, data=data,
                     headers={"Content-Type": "application/json"})
    SigV4Auth(boto3.Session().get_credentials(), "es", REGION).add_auth(req)
    r = urllib.request.Request(url, data=data.encode(), method="POST",
                               headers=dict(req.headers.items()))
    with urllib.request.urlopen(r, timeout=20) as resp:
        return json.loads(resp.read().decode())


def lambda_handler(event, context):
    q = ((event.get("queryStringParameters") or {}).get("q") or "").strip()
    if not q:
        return {"statusCode": 200, "headers": CORS, "body": json.dumps({"results": []})}
    kws = lex_keywords(q)
    # Lex may return only some of the keywords; merge with the parsed ones
    terms = []
    for k in kws + fallback_keywords(q):
        for w in fallback_keywords(k) or [k.lower()]:
            if w not in terms:
                terms.append(w)
    print("query", q, "terms", terms)
    if not terms:
        return {"statusCode": 200, "headers": CORS, "body": json.dumps({"results": []})}
    hits = es_search(terms).get("hits", {}).get("hits", [])
    results = []
    for h in hits:
        s = h["_source"]
        results.append({
            "url": "https://%s.s3.amazonaws.com/%s" % (s["bucket"], urllib.parse.quote(s["objectKey"])),
            "labels": s.get("labels", []),
        })
    return {"statusCode": 200, "headers": CORS, "body": json.dumps({"results": results})}

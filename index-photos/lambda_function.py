import json, os, urllib.request, urllib.parse
from datetime import datetime
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

REGION = os.environ.get("AWS_REGION", "us-east-1")
ES = os.environ["ES_ENDPOINT"].rstrip("/")
if not ES.startswith("http"):
    ES = "https://" + ES
s3 = boto3.client("s3")
rek = boto3.client("rekognition")


def es_put(path, body):
    url = ES + path
    data = json.dumps(body)
    req = AWSRequest(method="PUT", url=url, data=data,
                     headers={"Content-Type": "application/json"})
    SigV4Auth(boto3.Session().get_credentials(), "es", REGION).add_auth(req)
    r = urllib.request.Request(url, data=data.encode(), method="PUT",
                               headers=dict(req.headers.items()))
    with urllib.request.urlopen(r, timeout=20) as resp:
        return resp.read().decode()


def lambda_handler(event, context):
    for rec in event.get("Records", []):
        bucket = rec["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(rec["s3"]["object"]["key"])
        labels = []
        try:
            out = rek.detect_labels(
                Image={"S3Object": {"Bucket": bucket, "Name": key}},
                MaxLabels=20, MinConfidence=70)
            labels = [l["Name"].lower() for l in out["Labels"]]
        except Exception as e:
            print("Rekognition error", e)
        head = s3.head_object(Bucket=bucket, Key=key)
        custom = head.get("Metadata", {}).get("customlabels", "")
        for c in custom.split(","):
            c = c.strip().lower()
            if c and c not in labels:
                labels.append(c)
        doc = {
            "objectKey": key,
            "bucket": bucket,
            "createdTimestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
            "labels": labels,
        }
        print("Indexing", json.dumps(doc))
        print(es_put("/photos/_doc/" + urllib.parse.quote(bucket + "_" + key, safe=""), doc))
    return {"statusCode": 200, "body": json.dumps("indexed")}

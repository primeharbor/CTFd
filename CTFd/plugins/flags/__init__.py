import re
import os
import json
import boto3
from botocore.exceptions import ClientError

import logging
lookup_logger = logging.getLogger("LookupFlag")
lookup_logger.setLevel(getattr(logging, os.getenv('LOG_LEVEL', default='INFO')))
logging.getLogger('botocore').setLevel(logging.WARNING)
logging.getLogger('boto3').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.basicConfig()

from CTFd.utils.logging import log
from CTFd.plugins import register_plugin_assets_directory
from CTFd.utils.user import (
    authed,
    get_current_team,
    get_current_team_attrs,
    get_current_user,
    get_current_user_attrs,
    is_admin,
)

class FlagException(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return self.message


class BaseFlag(object):
    name = None
    templates = {}

    @staticmethod
    def compare(self, saved, provided):
        return True


class CTFdStaticFlag(BaseFlag):
    name = "static"
    templates = {  # Nunjucks templates used for key editing & viewing
        "create": "/plugins/flags/assets/static/create.html",
        "update": "/plugins/flags/assets/static/edit.html",
    }

    @staticmethod
    def compare(chal_key_obj, provided):
        saved = chal_key_obj.content
        data = chal_key_obj.data

        if len(saved) != len(provided):
            return False
        result = 0

        if data == "case_insensitive":
            for x, y in zip(saved.lower(), provided.lower()):
                result |= ord(x) ^ ord(y)
        else:
            for x, y in zip(saved, provided):
                result |= ord(x) ^ ord(y)
        return result == 0


class CTFdRegexFlag(BaseFlag):
    name = "regex"
    templates = {  # Nunjucks templates used for key editing & viewing
        "create": "/plugins/flags/assets/regex/create.html",
        "update": "/plugins/flags/assets/regex/edit.html",
    }

    @staticmethod
    def compare(chal_key_obj, provided):
        saved = chal_key_obj.content
        data = chal_key_obj.data

        try:
            if data == "case_insensitive":
                res = re.match(saved, provided, re.IGNORECASE)
            else:
                res = re.match(saved, provided)
        # TODO: this needs plugin improvements. See #1425.
        except re.error as e:
            raise FlagException("Regex parse error occured") from e

        return res and res.group() == provided

class LookupFlag(BaseFlag):
    name = "lookup"
    templates = {  # Nunjucks templates used for key editing & viewing
        "create": "/plugins/flags/assets/lookup/create.html",
        "update": "/plugins/flags/assets/lookup/edit.html",
    }

    @staticmethod
    def compare(chal_key_obj, provided):
        lookup_logger.debug("ENTER LookupFlag")
        saved = chal_key_obj.content
        s3_prefix = chal_key_obj.data
        bucket = os.environ['FLAG_BUCKET']
        user = get_current_user()
        team = get_current_team()

        # 2. Lookup the Team Account Name/ID
        team_env = get_team_env("AWSAccountName", team)
        if team_env is None:
            raise FlagException("No AWSAccountName Defined!")
        lookup_logger.info(f"S3 Object: s3://{bucket}/{s3_prefix}/{team_env}.json")

        # 3. Get the terraform output
        lookup_logger.info(f"Expected Value Key: {saved}")
        flag_data = get_object(bucket, f"{s3_prefix}/{team_env}.json")
        if flag_data is None:
            raise FlagException("Could not find flag data file in S3")

        # 4. Find the Key (chal_key_obj.content)
        expected_value = flag_data[chal_key_obj.content]['value']

        # 5. Compare the Value from TF Output and provided
        lookup_logger.info(f"Submitted: {provided} - Expected: {expected_value}")
        if provided == expected_value:
            return True
        return False

def get_object(bucket, obj_key):
    '''get the object to index from S3 and return the parsed json'''
    s3 = boto3.client('s3')
    try:
        response = s3.get_object(
            Bucket=bucket,
            Key=obj_key
        )
        return(json.loads(response['Body'].read()))
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            lookup_logger.error("Unable to find resource s3://{}/{}".format(bucket, obj_key))
        else:
            lookup_logger.error("Error getting resource s3://{}/{}: {}".format(bucket, obj_key, e))
        return(None)

def get_team_env(env_name, team):
    for field in team.field_entries:
        if field.name == env_name:
            return(field.value)
    return(None)


FLAG_CLASSES = {"static": CTFdStaticFlag, "regex": CTFdRegexFlag,  "lookup": LookupFlag}


def get_flag_class(class_id):
    cls = FLAG_CLASSES.get(class_id)
    if cls is None:
        raise KeyError
    return cls


def load(app):
    register_plugin_assets_directory(app, base_path="/plugins/flags/assets/")

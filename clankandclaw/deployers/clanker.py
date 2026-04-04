from clankandclaw.models.token import DeployRequest


def build_clanker_payload(deploy_request: DeployRequest) -> dict:
    return {
        "name": deploy_request.token_name,
        "symbol": deploy_request.token_symbol,
        "image": deploy_request.image_uri,
        "tokenAdmin": deploy_request.token_admin,
        "fees": {
            "type": "static",
            "clankerFee": deploy_request.tax_bps,
            "pairedFee": deploy_request.tax_bps,
        },
        "rewards": {
            "recipients": [
                {
                    "admin": deploy_request.token_admin,
                    "recipient": deploy_request.fee_recipient,
                    "bps": 10000,
                    "token": "Both",
                }
            ]
        },
        "metadataUri": deploy_request.metadata_uri,
    }

"""Provision an owner without sending invitations or exposing credentials."""

from __future__ import annotations

import argparse
import secrets

import boto3


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-pool-id", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    email = args.email.strip().lower()
    if "@" not in email or any(char.isspace() for char in email):
        parser.error("A valid owner email is required.")
    client = boto3.client("cognito-idp", region_name=args.region)
    created = False
    try:
        user = client.admin_get_user(UserPoolId=args.user_pool_id, Username=email)
    except client.exceptions.UserNotFoundException:
        response = client.admin_create_user(
            UserPoolId=args.user_pool_id,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
            MessageAction="SUPPRESS",
        )
        user = response["User"]
        created = True
    if created or user.get("UserStatus") == "FORCE_CHANGE_PASSWORD":
        # The owner chooses their own password through the verified email recovery
        # flow. This random bootstrap password is neither stored nor printed.
        client.admin_set_user_password(
            UserPoolId=args.user_pool_id,
            Username=email,
            Password=secrets.token_urlsafe(48) + "aA1!",
            Permanent=True,
        )
    client.admin_add_user_to_group(
        UserPoolId=args.user_pool_id,
        Username=email,
        GroupName="analytics-owners",
    )
    print("Owner access is configured. No invitation was sent.")
    if created:
        print(
            "Use Forgot your password? on the sign-in page to set your password, then enroll MFA."
        )


if __name__ == "__main__":
    main()

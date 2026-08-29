from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import boto3

from taxhance_pii.domain import QueueMessage


@dataclass(frozen=True)
class ReceivedMessage:
    task: QueueMessage
    receipt_handle: str


class TaskQueue(Protocol):
    def enqueue(self, message: QueueMessage) -> None: ...

    def receive(self, wait_seconds: int = 20) -> ReceivedMessage | None: ...

    def acknowledge(self, message: ReceivedMessage) -> None: ...

    def renew(self, message: ReceivedMessage) -> None: ...

    def release(self, message: ReceivedMessage, delay_seconds: int = 30) -> None: ...


class LocalTaskQueue:
    """SQLite status rows are the local queue; these methods intentionally do nothing."""

    def enqueue(self, message: QueueMessage) -> None:
        del message

    def receive(self, wait_seconds: int = 20) -> ReceivedMessage | None:
        del wait_seconds
        return None

    def acknowledge(self, message: ReceivedMessage) -> None:
        del message

    def renew(self, message: ReceivedMessage) -> None:
        del message

    def release(self, message: ReceivedMessage, delay_seconds: int = 30) -> None:
        del message, delay_seconds


class SqsTaskQueue:
    def __init__(self, queue_url: str, region: str, visibility_timeout: int) -> None:
        self.queue_url = queue_url
        self.visibility_timeout = visibility_timeout
        self.client = boto3.client("sqs", region_name=region)

    def enqueue(self, message: QueueMessage) -> None:
        self.client.send_message(QueueUrl=self.queue_url, MessageBody=message.model_dump_json())

    def receive(self, wait_seconds: int = 20) -> ReceivedMessage | None:
        response = self.client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=min(wait_seconds, 20),
            VisibilityTimeout=self.visibility_timeout,
            AttributeNames=["ApproximateReceiveCount"],
        )
        messages = response.get("Messages", [])
        if not messages:
            return None
        raw = messages[0]
        try:
            body = json.loads(raw["Body"])
            task = QueueMessage.model_validate(body)
        except (json.JSONDecodeError, ValueError):
            self.client.delete_message(
                QueueUrl=self.queue_url,
                ReceiptHandle=raw["ReceiptHandle"],
            )
            return None
        return ReceivedMessage(task=task, receipt_handle=str(raw["ReceiptHandle"]))

    def acknowledge(self, message: ReceivedMessage) -> None:
        self.client.delete_message(
            QueueUrl=self.queue_url,
            ReceiptHandle=message.receipt_handle,
        )

    def renew(self, message: ReceivedMessage) -> None:
        self.client.change_message_visibility(
            QueueUrl=self.queue_url,
            ReceiptHandle=message.receipt_handle,
            VisibilityTimeout=self.visibility_timeout,
        )

    def release(self, message: ReceivedMessage, delay_seconds: int = 30) -> None:
        self.client.change_message_visibility(
            QueueUrl=self.queue_url,
            ReceiptHandle=message.receipt_handle,
            VisibilityTimeout=delay_seconds,
        )

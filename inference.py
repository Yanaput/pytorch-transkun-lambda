import os
import json
import boto3
import subprocess
from datetime import datetime
from transkun.transcribe import transcribe

s3 = boto3.client('s3')
COMMON_BUCKET_NAME = '<COMMON_BUCKET_NAME>'
AUTH_BUCKET_NAME = '<AUTH_BUCKET_NAME>'

db = boto3.client('dynamodb')
TABLE_NAME = '<TABLE_NAME>'

ws_api_gateway_endpoint = "<API URL>"
clientWebsocket = boto3.client("apigatewaymanagementapi", endpoint_url=ws_api_gateway_endpoint)


def send_websocket_message(connection_id, job_id, status, is_auth, s3_key_pdf=None, s3_key_midi=None):
    """
    Sends a WebSocket notification when transcription is completed or failed.
    """
    message = {"job_id": job_id, "status": status}
    if s3_key_pdf:
        message["pdf_url"] = f"{s3_key_pdf}"
    if s3_key_midi:
        message["midi_url"] = f"{s3_key_midi}"

    try:
        clientWebsocket.post_to_connection(ConnectionId=connection_id, Data=json.dumps(message))
        print("WebSocket connection is active.")
    except Exception as e:
        if isinstance(e, clientWebsocket.exceptions.GoneException):
            if not is_auth:
                raise
            return



def convert_midi_to_sheet(midi_path, output_path):
    """
        Take midi_path and output_path then convert midi to music sheet.
        Raise error if fail
    """
    print("Converting to music sheet")

    result = subprocess.run(
        ['xvfb-run', '-a', '-s', '-screen 0 1024x768x24', 'musescore', '--export-to', output_path, midi_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to write pdf: {result.stderr}")
    print("Conversion successful")


def update_progress_db(user_id, job_id, new_status, s3_key_pdf=None, s3_key_midi=None, exe_time=None):
    update_expr = "SET progress = :new_status, #ts = :new_ts"
    expr_attr_vals = {
        ":new_status": {"S": new_status},
        ":new_ts": {"S": datetime.utcnow().isoformat() + "Z"}
    }

    if s3_key_pdf is not None and s3_key_midi is not None and exe_time is not None:
        update_expr += ", s3_pdf = :pdf_key, s3_midi = :midi_key, execution_time = :exe_time"
        expr_attr_vals.update({
            ":pdf_key": {"S": s3_key_pdf},
            ":midi_key": {"S": s3_key_midi},
            ":exe_time": {"S": str(exe_time)}
        })

    db.update_item(
        TableName=TABLE_NAME,
        Key={
            "user_id": {"S": user_id},
            "job_id": {"S": job_id},
        },
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_attr_vals,
        ConditionExpression="attribute_exists(#uid)",
        ExpressionAttributeNames={
            "#uid": "user_id",
            "#ts": "timestamp"
        }
    )


def process_audio(connection_id, job_id, audio_key, is_auth, user_id):
    try:
        start_time = datetime.now()
        audio_filename = os.path.basename(audio_key)
        local_audio_path = f"/tmp/{audio_filename}"

        send_websocket_message(connection_id, job_id, "Downloading", is_auth)
        s3.download_file(COMMON_BUCKET_NAME, audio_key, local_audio_path)

        local_midi_path = f"/tmp/{audio_filename}.mid"
        local_pdf_path = f"/tmp/{audio_filename}.pdf"

        send_websocket_message(connection_id, job_id,"Transcribing", is_auth)
        if is_auth:
            update_progress_db(user_id, job_id, "Transcribing")
        transcribe(local_audio_path, local_midi_path)

        send_websocket_message(connection_id, job_id, "Converting to sheet", is_auth)
        if is_auth:
            update_progress_db(user_id, job_id, "Converting to sheet")
        convert_midi_to_sheet(local_midi_path, local_pdf_path)

        s3_output_path = audio_key.replace("uploads/", "").rsplit(".", 1)[0]

        # S3 paths
        s3_key_midi = f"{s3_output_path}.mid"
        s3_key_pdf = f"{s3_output_path}.pdf"

        if is_auth:
            update_progress_db(user_id, job_id, "Saving outputs")
        s3.upload_file(local_midi_path, COMMON_BUCKET_NAME if not is_auth else AUTH_BUCKET_NAME, s3_key_midi)
        s3.upload_file(local_pdf_path, COMMON_BUCKET_NAME if not is_auth else AUTH_BUCKET_NAME, s3_key_pdf)

        exe_time = datetime.now()-start_time

        if is_auth:
            update_progress_db(user_id, job_id, "Completed", s3_key_pdf, s3_key_midi, exe_time)
        send_websocket_message(connection_id, job_id, "Completed", is_auth, s3_key_pdf, s3_key_midi)
        print("Processing complete.")

    except Exception as e:
        print(f"Failed to send failure notification: {str(e)}")
        if is_auth:
            update_progress_db(user_id, job_id, "Failed")
        send_websocket_message(connection_id, job_id, "failed", is_auth)


def lambda_handler(event, context):
    """
        Handles API Gateway request and background execution
        Since API Gateway only wait for 30s, but transcription might take up to 15min(Lambda max)
        function have to return status and job_id first.
        Then invoke itself again for transcription.
        Use job_id and connection_id and websocket to track transcription process.
    """
    try:
        print("Received event:", json.dumps(event, indent=4))

        # If it's a background task, process the audio
        if event.get("background"):
            print("Running in background mode")
            audio_key = event.get("audio_key")
            job_id = event.get("job_id")
            connection_id = event.get("connection_id")
            is_auth = event.get("isAuth")
            user_id = event.get("userId")

            process_audio(connection_id, job_id, audio_key, is_auth, user_id)

            return {"statusCode": 200, "body": json.dumps({"message": "Processing complete"})}

        # Otherwise, it's a new API request

        print("query_params")
        if "body" not in event:
            raise ValueError("Missing 'body' in request")

        body = json.loads(event["body"])
        audio_key = body.get("audio_key")
        job_id = body.get("job_id")
        connection_id = body.get("connection_id")
        is_auth = body.get("isAuth")
        user_id = body.get("userId")
        file_name = body.get("file_name")

        # Invoke itself asynchronously for background processing
        lambda_client = boto3.client("lambda")
        lambda_response = lambda_client.invoke(
            FunctionName=context.function_name,
            InvocationType="Event",  # Asynchronous execution
            Payload=json.dumps({
                "audio_key": audio_key,
                "job_id": job_id,
                "connection_id": connection_id,
                "isAuth": is_auth,
                "userId": user_id,
                "background": True
            })
        )
        print(lambda_response["StatusCode"])

        if lambda_response["StatusCode"] not in [200, 202]:
            error_message = f"Failed to invoke async Lambda. Response: {json.dumps(lambda_response)}"
            send_websocket_message(connection_id, job_id, "Failed", is_auth)
            raise RuntimeError(error_message)

        # Send message to WS and return to informs inference starting
        send_websocket_message(connection_id, job_id, "Starting", is_auth)
        print("Returning: Processing started")
        if is_auth:
            db.put_item(
                TableName=TABLE_NAME,
                Item={
                    "user_id": {"S": user_id},
                    "job_id": {"S": job_id},
                    "audio_filename": {"S": file_name},
                    "progress": {"S": "Downloading"},
                    "timestamp": {"S": datetime.utcnow().isoformat() + "Z"}
                }
            )

        return {
            "statusCode": 202,
            "body": json.dumps({
                "message": "Processing started",
                "job_id": job_id
            })
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }

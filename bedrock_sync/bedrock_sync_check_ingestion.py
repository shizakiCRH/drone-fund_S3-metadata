import boto3
import os
import time

client = boto3.client("bedrock-agent")

def lambda_handler(event, context):
    kb_id = os.environ["KNOWLEDGE_BASE_ID"]
    data_source_id = event["dataSourceId"]
    job_id = event["jobId"]

    response = client.get_ingestion_job(
        knowledgeBaseId=kb_id,
        dataSourceId=data_source_id,
        ingestionJobId=job_id
    )

    status = response["ingestionJob"]["status"]

    print(status)
    
    if status == "COMPLETE":
        return {"status": "COMPLETE", "dataSourceId": data_source_id}
    elif status in ["FAILED", "STOPPED"]:
        raise Exception(f"Ingestion failed or stopped for {data_source_id}: {status}")
    elif status == "STOPPING":
        # Option: treat as a retryable exception
        raise Exception("Ingestion stopping – retry later")
    else:
        # IN_PROGRESS or other → 再試行をStep Functionsに任せる
        raise Exception("IN_PROGRESS")

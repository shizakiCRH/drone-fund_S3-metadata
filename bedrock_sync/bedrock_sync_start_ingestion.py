import boto3
import os

client = boto3.client("bedrock-agent")

def lambda_handler(event, context):
    data_source_id = event["dataSourceId"]
    kb_id = os.environ["KNOWLEDGE_BASE_ID"]

    response = client.start_ingestion_job(
        knowledgeBaseId=kb_id,
        dataSourceId=data_source_id,
    )
    
    return {
        "jobId": response["ingestionJob"]["ingestionJobId"],
        "dataSourceId": data_source_id
    }

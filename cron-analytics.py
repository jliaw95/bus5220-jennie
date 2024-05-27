#!/home/jl140/.virtualenvs/cronenv/bin/python
import os
import time
import datetime
from pymongo import MongoClient
from openai import OpenAI
from dotenv import load_dotenv

# Load .env
project_folder = os.path.expanduser('/home/jl140/bus5220')
load_dotenv(os.path.join(project_folder, '.env'))

# MongoDB Atlas connection
mongo_uri = os.getenv('MDB_URI')
mongo_client = MongoClient(mongo_uri, connectTimeoutMS=30000, socketTimeoutMS=None, connect=False, maxPoolsize=1)
chat_db = mongo_client['chat_db']
analytics_db = mongo_client['analytics_db']

# OpenAI API configuration
client = OpenAI(api_key=(os.getenv('OPENAI_SECRET')))

def analyze_chats():
    # Query the 'chats' collection
    chats = chat_db['chats'].find(
        filter={},
        projection={'chatbot_response': 1, 'user_input': 1, '_id': 0},
        sort=[('timestamp', -1)]
    )

    # Combine all chat responses into a single object
    chat_data = {
        'user_inputs': [],
        'chatbot_responses': []
    }
    for chat in chats:
        if 'user_input' in chat:
            chat_data['user_inputs'].append(chat['user_input'])
        if 'chatbot_response' in chat:
            chat_data['chatbot_responses'].append(chat['chatbot_response'])

    # Create a new Thread in the OpenAI API
    thread = client.beta.threads.create(
        tool_resources={
            "file_search": {
                "vector_store_ids": ["vs_d3dVw9V0IUAnujI2hNZWy693"]
                }
            }
    )
    thread_id = thread.id

    # Create a new Message in the Thread
    message = client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=f"User Inputs: {chat_data['user_inputs']}\nChatbot Responses: {chat_data['chatbot_responses']}"
    )

    # Create a Run and wait for completion
    run = client.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id='asst_4AA14TSM3JDJw8CI5PPmdTPa',
    )
    while run.status == "queued" or run.status == "in_progress":
        run = client.beta.threads.runs.retrieve(
            thread_id=thread_id,
            run_id=run.id,
        )
        time.sleep(0.5)

    # Retrieve the Assistant's response from the Thread's messages
    messages = client.beta.threads.messages.list(thread_id=thread_id, order="asc")
    assistant_response = messages.data[-1].content[0].text.value

    # Store the assistant response in the 'compliance_results' collection
    analytics_db['compliance_results'].insert_one({
        'chat_data': chat_data,
        'assistant_response': assistant_response,
        'timestamp': datetime.now()
    })

if __name__ == '__main__':
    analyze_chats()
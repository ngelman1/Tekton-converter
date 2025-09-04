import os
import sys
import argparse
from pathlib import Path
import google.generativeai as genai
from termcolor import cprint
from llama_stack_client import LlamaStackClient


# --- Config ---
VECTOR_DB_ID = "tekton_docs_vector_db"
client = LlamaStackClient(base_url="http://localhost:8321")

try:
    api_key = os.getenv('GEMINI_API_KEY')
    if api_key:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-pro")
        api_key_available = True
    else: 
        cprint("GEMINI_API_KEY NOT SET", "red") # This line is good
        api_key_available = False
except Exception as e:
    cprint(f"Error configuring Google AI: {e}", "red")
    api_key_available = False


def read_jenkinsfile(jenkinsfile_path: str) -> str:
    jenkinsfile_path = Path(jenkinsfile_path)
    if not jenkinsfile_path.is_file():
        print(f"Error: Jenkinsfile not found at {jenkinsfile_path}")
        sys.exit(1)

    with open(jenkinsfile_path, 'r', encoding='utf-8') as file:
        return file.read()


def generate_without_RAG(jenkinsfile_content: str) -> str:
    prompt = f"""
        You are a Tekton expert. Convert the following Jenkinsfile into valid Tekton v1 YAML.

        Requirements:
        1. Break down the pipeline into separate Tekton resources:
        - One or more Task objects (one per Jenkins stage/step).
        - A Pipeline object that references those Tasks.
        - A PipelineRun object that executes the Pipeline.
        2. Use only Tekton v1 syntax.
        3. Scripts must be strings (not arrays).
        4. Output MUST be multiple YAML documents separated by '---':
        first the Task(s), then the Pipeline, then the PipelineRun.
        5. Output ONLY raw YAML, no markdown formatting or explanations.

        Jenkinsfile:
        {jenkinsfile_content}
        """
    response = model.generate_content(prompt)
    return response.text


# Assuming VECTOR_DB_ID is defined as a global variable.
def query_RAG_and_print(client: LlamaStackClient, query: str):
    try:
        cprint(f"Querying RAG for: '{query}'", "blue")
        
        results = client.tool_runtime.rag_tool.query(
            content=[{"type": "text", "text": query}], 
            vector_db_ids=["tekton_docs_vector_db"], # This is still required
        )

        
        if not results or not results.content:
            cprint("No relevant documents found.", "yellow")
            return []
        
        cprint(f"Found {len(results.content)} relevant documents:", "green")
        for i, doc in enumerate(results.content, 1):
            source = doc.get("metadata", {}).get("source", "<unknown>")
            score = doc.get("score", "<no-score>")
            print(f"  {i}. Source: {source} | Score: {score}")

        return results.content
    
    except Exception as e:
        cprint(f"❌ Error querying RAG: {e}", "red")
        return []

def build_prompt_with_rag(jenkinsfile_content: str, relevant_docs: list) -> str:
    context_texts = [doc.get("content", "") for doc in relevant_docs]
    combined_context = "\n\n".join(context_texts)

    prompt = f"""
        You are a Tekton expert. Convert the following Jenkinsfile into valid Tekton v1 YAML.

        Use the following reference material from the knowledge base (RAG results) to guide the conversion:

        {combined_context}

        Requirements:
        1. Break down the pipeline into separate Tekton resources:
        - One or more Task objects (one per Jenkins stage/step).
        - A Pipeline object that references those Tasks.
        - A PipelineRun object that executes the Pipeline.
        2. Use only Tekton v1 syntax.
        3. Scripts must be strings (not arrays).
        4. Output MUST be multiple YAML documents separated by '---':
        first the Task(s), then the Pipeline, then the PipelineRun.
        5. Output ONLY raw YAML, no markdown formatting or explanations.

        Jenkinsfile:
        {jenkinsfile_content}
        """
    return prompt


def generate_with_RAG(jenkinsfile_content: str, relevant_docs: list) -> str:
    prompt = build_prompt_with_rag(jenkinsfile_content, relevant_docs)
    response = model.generate_content(prompt)
    return response.text


def main():
    parser = argparse.ArgumentParser(description='Generate Tekton pipeline from Jenkinsfile with and without RAG queries')
    parser.add_argument('jenkinsfile_path', help='Path to the Jenkinsfile to convert')
    args = parser.parse_args()

    jenkinsfile_content = read_jenkinsfile(args.jenkinsfile_path)

    conversion_without_rag = generate_without_RAG(jenkinsfile_content)
    print("\n=== Conversion WITHOUT RAG ===")
    print(conversion_without_rag)

    
    print("\n--- Query RAG Results ---")
    relevant_docs = query_RAG_and_print(client, "How to convert Jenkinsfile to Tekton Pipeline")

    
    if relevant_docs:
        conversion_with_rag = generate_with_RAG(jenkinsfile_content, relevant_docs)
        print("\n=== Conversion WITH RAG ===")
        print(conversion_with_rag)


if __name__ == "__main__":
    main()

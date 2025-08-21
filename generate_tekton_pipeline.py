import os
import sys
import argparse
import json
from pathlib import Path
import google.generativeai as genai
from termcolor import cprint


try:
    api_key = os.getenv('GOOGLE_API_KEY')
    if api_key:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-pro")
        api_key_available = True
    else: 
        cprint("GOOGLE_API_KEY NOT SET", "red")
        api_key_available = False
except Exception as e:
    cprint(f"Error configuring Google AI: {e}", "red")
    api_key_available = False




def read_jenkinsfile(jenkinsfile_path: str) -> str:
    jenkinsfile_path = Path(args.jenkinsfile_path)
    if not jenkinsfile_path.is_file():
        print(f"Error: Jenkinsfile not found at {jenkinsfile_path}")
        sys.exit(1)

    with open(jenkinsfile_path, 'r', encoding='utf-8') as file:
        jenkinsfile_content = file.read()
    return jenkinsfile_content



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




def search_rag_and_print(rag_search_fn, query: str):
    """
    Searches the RAG store using a query and prints the results.
    
    Returns:
        List of file paths (Path objects)
    """
    relevant_files = rag_search_fn(query)
    if not relevant_files:
        print("❌ No relevant files found in RAG.")
        return []

    print("\n📂 Found relevant files in RAG:")
    for f in relevant_files:
        print("  -", f)

    return [Path(f) for f in relevant_files if Path(f).is_file()]



    

def main():
    parser = argparse.ArgumentParser(description='Generate Tekton pipeline from Jenkinsfile with and without RAG queries')
    parser.add_argument('jenkinsfile_path', help='Path to the Jenkinsfile to convert')
    args = parser.parse_args()

    jenkinsfile_content = read_jenkinsfile(args.jenkinsfile_path)

    # Generate and print outputs
    prompt_without_rag = generate_without_RAG(jenkinsfile_content)
    prompt_with_rag = generate_prompt_with_rag(jenkinsfile_content)
    final_tekton_pipeline = generate_final_tekton_pipeline(jenkinsfile_content)

    print("\nPrompt without RAG:")
    print(prompt_without_rag)
    print("\nPrompt with RAG:")
    print(prompt_with_rag)
    print("\nFinal Tekton Pipeline:")
    print(final_tekton_pipeline)

if __name__ == "__main__":
    main()

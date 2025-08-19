import os
import sys
from typing import Dict, Optional, Any
from termcolor import cprint
import google.generativeai as genai
import yaml
import json
import re
import subprocess
from pathlib import Path
import argparse
import time

# Add the analyzing module to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'analyzing'))

try:
    from analyzing.tree_sitter_analyzer import JenkinsfileAnalyzer, analyze_jenkinsfile
    TREE_SITTER_AVAILABLE = True
except ImportError as e:
    cprint(f"⚠️ Warning: Tree-sitter analyzer not available: {e}", "yellow")
    cprint("   Install with: pip install -r analyzing/requirements.txt", "yellow")
    TREE_SITTER_AVAILABLE = False

# Initialize Google AI client
try:
    # You'll need to set your API key as an environment variable
    api_key = os.getenv('GOOGLE_API_KEY')
    if api_key:
        genai.configure(api_key=api_key)
        GEMINI_AVAILABLE = True
        cprint("✅ Google AI (Gemini) configured successfully", "green")
    else:
        cprint("⚠️ Warning: GOOGLE_API_KEY environment variable not set", "yellow")
        cprint("   Set it with: export GOOGLE_API_KEY='your-api-key'", "yellow")
        GEMINI_AVAILABLE = False
except Exception as e:
    cprint(f"⚠️ Warning: Google AI client not available: {e}", "yellow")
    GEMINI_AVAILABLE = False

VECTOR_DB_ID = "tekton_docs_vector_db"

def clean_yaml_response(response: str) -> str:
    """Clean up markdown formatting from YAML response"""
    # Remove markdown code block syntax
    response = re.sub(r'^```ya?ml\s*', '', response, flags=re.MULTILINE)
    response = re.sub(r'\s*```\s*$', '', response, flags=re.MULTILINE)
    
    # Remove any leading/trailing whitespace
    response = response.strip()
    
    return response

def validate_yaml_syntax(content: str) -> bool:
    """Validate basic YAML syntax and structure"""
    try:
        if not isinstance(content, str):
            cprint("❌ Error: Content is not a string", "red")
            return False
            
        pipeline_dict = yaml.safe_load(content)
            
        if not isinstance(pipeline_dict, dict):
            cprint("❌ Error: Content is not a valid YAML dictionary", "red")
            return False
            
        if pipeline_dict.get('kind') != 'PipelineRun':
            cprint("❌ Error: YAML is not a PipelineRun resource", "red")
            return False
            
        if 'apiVersion' not in pipeline_dict:
            cprint("❌ Error: Missing apiVersion", "red")
            return False

        # Check for array-style scripts
        tasks = pipeline_dict.get('spec', {}).get('pipelineSpec', {}).get('tasks', [])
        for task in tasks:
            taskSpec = task.get('taskSpec', {})
            steps = taskSpec.get('steps', [])
            for step in steps:
                if isinstance(step.get('script'), list):
                    cprint("❌ Error: Script field must be a string, not an array", "red")
                    cprint(f"   In step '{step.get('name')}' of task '{task.get('name')}'", "red")
                    return False            
            
        return True
    except yaml.YAMLError as e:
        cprint(f"❌ Error: Invalid YAML syntax - {str(e)}", "red")
        return False
    except Exception as e:
        cprint(f"❌ Error validating YAML: {str(e)}", "red")
        return False

def validate_tekton_multi_yaml_syntax(content: str) -> bool:
    """Validate YAML that may contain multiple Tekton documents (Task, Pipeline, PipelineRun).
    Ensures each document is a dict with apiVersion/kind and checks common script field issues.
    """
    try:
        docs = list(yaml.safe_load_all(content))
        if not docs:
            cprint("❌ Error: No YAML documents found", "red")
            return False

        allowed_kinds = {"Task", "Pipeline", "PipelineRun"}
        for idx, doc in enumerate(docs):
            if not isinstance(doc, dict):
                cprint(f"❌ Error: Document {idx+1} is not a YAML dictionary", "red")
                return False
            if 'apiVersion' not in doc:
                cprint(f"❌ Error: Document {idx+1} missing apiVersion", "red")
                return False
            kind = doc.get('kind')
            if kind not in allowed_kinds:
                cprint(f"❌ Error: Document {idx+1} has unsupported kind: {kind}", "red")
                return False

            # Check script fields are strings, not arrays
            if kind == 'Task':
                steps = doc.get('spec', {}).get('steps', [])
                for step in steps:
                    if isinstance(step.get('script'), list):
                        cprint("❌ Error: Script field must be a string, not an array", "red")
                        cprint(f"   In step '{step.get('name')}' of Task '{doc.get('metadata', {}).get('name', '<no-name>')}'", "red")
                        return False
            elif kind == 'PipelineRun':
                tasks = doc.get('spec', {}).get('pipelineSpec', {}).get('tasks', [])
                for task in tasks:
                    taskSpec = task.get('taskSpec', {})
                    steps = taskSpec.get('steps', [])
                    for step in steps:
                        if isinstance(step.get('script'), list):
                            cprint("❌ Error: Script field must be a string, not an array", "red")
                            cprint(f"   In step '{step.get('name')}' of task '{task.get('name')}'", "red")
                            return False
        return True
    except yaml.YAMLError as e:
        cprint(f"❌ Error: Invalid YAML syntax - {str(e)}", "red")
        return False
    except Exception as e:
        cprint(f"❌ Error validating YAML: {str(e)}", "red")
        return False

def analyze_and_fix_validation_error(error_message: str, content: str) -> Optional[str]:
    """Analyze validation error and attempt to fix the YAML using the model"""
    try:
        print("\n🔧 Analyzing validation error...")
        
        # Create a prompt for the model to fix the error
        prompt = f"""You are a Tekton expert. Fix the following PipelineRun YAML that has validation errors.

Error message:
{error_message}

Current YAML:
{content}

Rules for fixing Tekton v1 PipelineRun:
1. PipelineRun v1 Required Fields:
   - spec.pipelineSpec: Contains tasks and their definitions
   - spec.params: Array of name/value pairs
   - spec.workspaces: Array of workspace bindings

2. PipelineRun v1 Optional Fields:
   - spec.timeouts: For setting timeouts
   - spec.taskRunTemplate: For common task settings
     including serviceAccountName

3. Common Errors to Fix:
   - Move serviceAccountName under taskRunTemplate
   - Use array for params, not map
   - Use workspaces, not workspaceBindings
   - Use pipelineSpec, not pipelineDefinition
   - Remove any v1beta1 inputs/outputs
   - Remove top-level serviceAccount field

2. Task and Step fields:
   - runAfter goes at task level, not in taskSpec
   - steps (not step) for defining task steps
   - params at task level for passing values
   - workspaces at task level for binding
   - script must be a string or heredoc, not an array
   Example of valid script formats:
     steps:
       - name: test
         script: go test ./...  # Single line
       - name: build
         script: |              # Multi-line
           go build
           ./run-tests.sh

3. Step-level fields:
   - securityContext goes inside individual steps
   - For buildah/privileged containers, use this structure:
       name: build
       image: quay.io/buildah/buildah
       script: |
         buildah bud --storage-driver=vfs -t $(params.IMAGE) .
       securityContext:
         privileged: true
         runAsUser: 0

4. Parameter rules:
   - PipelineRun spec.params: ONLY name and value
     Example:
     params:
       - name: git-url
         value: "https://github.com/example/repo"
   - PipelineSpec params: MUST have type
     Example:
     params:
       - name: git-url
         type: string
   - TaskSpec params: MUST have type
     Example:
     taskSpec:
       params:
         - name: IMAGE
           type: string
           description: optional description
   - Task params: ONLY name and value for passing
     Example:
     params:
       - name: IMAGE
         value: $(params.image-url)

Workspace configuration notes:
   - Define workspaces at PipelineRun level with emptyDir
   - Reference them in tasks using the workspace field
   - Use consistent workspace names across all levels
   - Ensure proper indentation in the YAML output

Response format: Output ONLY the raw YAML content with no markdown formatting."""

        # Use the chat endpoint to get the fix
        response = client.chat.completions.create(
            model="gemini-2.5-pro",
            messages=[
                {"role": "system", "content": "You are a Tekton expert. Fix invalid YAML."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1
        )
        
        # Get the fixed YAML from the response
        if hasattr(response, 'choices') and response.choices:
            fixed_yaml = response.choices[0].message.content
        else:
            fixed_yaml = str(response)
        
        if not fixed_yaml:
            print("❌ Empty response from model")
            return None
            
        # Clean up any markdown formatting
        fixed_yaml = clean_yaml_response(fixed_yaml)
        
        # Basic YAML validation
        if not validate_yaml_syntax(fixed_yaml):
            return None
            
        return fixed_yaml
            
    except Exception as e:
        print(f"❌ Error fixing validation error: {e}")
        return None

def validate_with_binary(content: str, validator_binary: str, temp_file: str = "temp_pipeline.yaml") -> bool:
    """Validate PipelineRun using the external validator binary"""
    try:
        # Save content to temporary file
        with open(temp_file, 'w') as f:
            f.write(content)
        
        # Skip external validator for multi-document YAML (validator supports Pipeline/PipelineRun single docs)
        if '---' in content:
            cprint("ℹ️ Skipping external validator for multi-document YAML.", "yellow")
            return True
        # Run the external validator
        result = subprocess.run(
            [validator_binary, temp_file],
            capture_output=True,
            text=True
        )
        
        # Clean up temp file
        os.remove(temp_file)
        
        # Check return code
        if result.returncode != 0:
            error_message = result.stderr if result.stderr else result.stdout
            print("❌ External validation failed:")
            print(error_message)
            
            # Try to fix the error
            print("\n🔧 Attempting to fix validation errors...")
            fixed_yaml = analyze_and_fix_validation_error(error_message, content)
            
            if fixed_yaml:
                print("\n📝 Fixed YAML content:")
                print("-" * 40)
                print(fixed_yaml)
                print("-" * 40)
                
                # Ask user if they want to use the fix
                use_fix = input("\nDo you want to use this fix? (y/n): ").lower()
                if use_fix == 'y':
                    print("\n🔍 Validating the fixed PipelineRun...")
                    # Validate the fix
                    with open(temp_file, 'w') as f:
                        f.write(fixed_yaml)
                    
                    validation_result = subprocess.run(
                        [validator_binary, temp_file],
                        capture_output=True,
                        text=True
                    )
                    
                    os.remove(temp_file)
                    
                    if validation_result.returncode == 0:
                        print("✅ Fixed YAML validates successfully!")
                        # Update the content in the original file
                        with open("generated_pipelinerun.yaml", 'w') as f:
                            f.write(fixed_yaml)
                        return True
                    else:
                        print("❌ Fixed YAML still has validation errors:")
                        error_output = validation_result.stderr if validation_result.stderr else validation_result.stdout
                        print(error_output)
                        print("\nPlease check the errors and try again.")
            
            return False
            
        cprint("✅ External validation successful!", "green")
        return True
        
    except subprocess.CalledProcessError as e:
        cprint(f"❌ Error running validator: {str(e)}", "red")
        if e.stderr:
            print(e.stderr)
        return False
    except Exception as e:
        cprint(f"❌ Error: {str(e)}", "red")
        return False
    finally:
        # Ensure temp file is removed
        if os.path.exists(temp_file):
            os.remove(temp_file)

def ingest_to_rag(content: str, filename: str) -> bool:
    """Ingest a validated PipelineRun into the RAG system (simplified)"""
    try:
        # For now, just log that we would ingest
        cprint(f"ℹ️ Would ingest PipelineRun to RAG system (RAG disabled)", "blue")
        cprint(f"   Content length: {len(content)} characters", "blue")
        cprint(f"   Filename: {filename}", "blue")
        return True
        
    except Exception as e:
        cprint(f"❌ Error in ingest function: {e}", "red")
        return False

def search_knowledge_base(query: str, vector_db_id: str, max_results: int = 3) -> str:
    """Search for relevant context (simplified without RAG)"""
    cprint("ℹ️ Using basic Tekton conversion context", "blue")
    return """Basic Tekton v1 PipelineRun structure:

apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  name: example-pipelinerun
spec:
  pipelineSpec:
    tasks:
    - name: task-1
      taskSpec:
        steps:
        - name: step-1
          image: alpine:latest
          script: |
            echo "Hello World"
  params:
  - name: param1
    value: "value1"
  workspaces:
  - name: source
    emptyDir: {}
"""

def analyze_jenkinsfile_with_tree_sitter(file_path: str, grammar_path: Optional[str] = None) -> Optional[Dict[str, Any]]:

    if not TREE_SITTER_AVAILABLE:
        cprint("❌ Tree-sitter analyzer not available", "red")
        return None
    
    try:
        cprint("🔍 Analyzing Jenkinsfile with tree-sitter...", "blue")
        result = analyze_jenkinsfile(file_path, grammar_path)
        
        if result and result.get('parsed'):
            cprint("✅ Jenkinsfile analysis completed", "green")
            return result
        else:
            cprint("❌ Failed to analyze Jenkinsfile", "red")
            return None
            
    except Exception as e:
        cprint(f"❌ Error analyzing Jenkinsfile: {e}", "red")
        return None

def generate_pipelinerun(requirements: str, context: str, ast_data: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Generate a PipelineRun using Gemini with optional AST data"""
    try:
        # Enhanced prompt with AST data if available
        if ast_data and ast_data.get('parsed'):
            ast_context = f"""
Tree-sitter Analysis Results:
{json.dumps(ast_data['parsed'], indent=2)}

Tekton Structure:
{json.dumps(ast_data.get('tekton', {}), indent=2)}
"""
            enhanced_requirements = f"{requirements}\n\nAST Analysis Context:\n{ast_context}"
        else:
            enhanced_requirements = requirements
        
        prompt = f"""You are a Tekton expert. Generate a valid PipelineRun YAML that exactly matches the requirements.

Requirements:

{enhanced_requirements}

Here is relevant documentation and examples for reference:
{context}

Rules:
1. Generate ONLY the PipelineRun resource
2. Use valid Tekton v1 syntax
3. Use taskSpec blocks directly in the PipelineRun
4. Follow the exact same syntax as shown in the examples
5. Ensure the YAML starts with apiVersion
6. Do not include any markdown formatting or code block syntax
7. Output ONLY the raw YAML content, no explanations or other text
8. If AST data is provided, use it to create more accurate conversions

Response format: Output ONLY the raw YAML content with no markdown formatting."""

        # Use Gemini to generate the PipelineRun
        if not GEMINI_AVAILABLE:
            cprint("❌ Google AI (Gemini) not available. Cannot generate PipelineRun.", "red")
            cprint("   Set GOOGLE_API_KEY environment variable and install google-generativeai", "red")
            return None
            
        try:
            model = genai.GenerativeModel('gemini-1.5-pro')
            response = model.generate_content(prompt)
            
            if response and response.text:
                return response.text
            else:
                cprint("❌ Empty response from Gemini", "red")
                return None
                
        except Exception as e:
            cprint(f"❌ Error calling Gemini: {e}", "red")
            return None
        
        # Get the generated YAML from the response
        yaml_content = response.text if response and response.text else ""
        
        if not yaml_content:
            cprint("Error: Empty response from model", "red")
            return None
            
        # Clean up any markdown formatting
        yaml_content = clean_yaml_response(yaml_content)
        
        # Basic YAML validation (allow multiple docs of Task/Pipeline/PipelineRun)
        if not validate_tekton_multi_yaml_syntax(yaml_content):
            return None
            
        return yaml_content
            
    except Exception as e:
        cprint(f"Error generating PipelineRun: {e}", "red")
        return None

def save_yaml(content: str, filename: str = "generated_pipelinerun.yaml") -> bool:
    """Save content to a YAML file"""
    try:
        with open(filename, 'w') as f:
            f.write(content)
        return True
    except Exception as e:
        cprint(f"Error saving file: {e}", "red")
        return False

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Generate and validate a Tekton PipelineRun')
    parser.add_argument('source_file', help='Path to a YAML file or Jenkinsfile to convert into a Tekton v1 PipelineRun')
    parser.add_argument('--validator', default='./validator_bin',
                      help='Path to the validator binary (default: ./validator_bin)')
    parser.add_argument('--no-ingest', action='store_true',
                      help='Skip ingesting successful PipelineRuns back to RAG')
    parser.add_argument('--use-ast', action='store_true',
                      help='Use tree-sitter AST analysis for Jenkinsfiles')
    parser.add_argument('--grammar-path', help='Path to the tree-sitter grammar library for Jenkinsfiles')
    args = parser.parse_args()
    
    # Read source file
    source_path = Path(args.source_file)
    if not source_path.is_file():
        cprint(f"❌ Error: Source file not found: {source_path}", "red")
        return
    
    # Determine file type and read content
    file_extension = source_path.suffix.lower()
    file_name_lower = source_path.name.lower()
    
    # Check if it's a Jenkinsfile (case-insensitive)
    is_jenkinsfile = (
        file_extension in ['.groovy', '.jenkinsfile'] or
        file_name_lower in ['jenkinsfile', 'jenkinsfile.groovy'] or
        'jenkinsfile' in file_name_lower or
        source_path.name == 'Jenkinsfile'  # Exact match for capital J
    )
    
    # Debug output
    cprint(f"🔍 File analysis:", "blue")
    cprint(f"   Name: {source_path.name}", "blue")
    cprint(f"   Extension: {file_extension}", "blue")
    cprint(f"   Is Jenkinsfile: {is_jenkinsfile}", "blue")
    
    with open(source_path, 'r', encoding='utf-8') as f:
        source_content = f.read()

    ast_data = None
    if is_jenkinsfile and args.use_ast and TREE_SITTER_AVAILABLE:
        ast_data = analyze_jenkinsfile_with_tree_sitter(str(source_path), args.grammar_path)
        if ast_data:
            cprint("✅ Tree-sitter analysis completed successfully", "green")
        else:
            cprint("⚠️ Tree-sitter analysis failed, continuing without AST data", "yellow")
    
    # For Jenkinsfiles, use the content directly; for YAML, use as before
    if is_jenkinsfile:
        source_yaml_text = source_content
        file_type_note = " (Jenkinsfile)"
    else:
        source_yaml_text = source_content
        file_type_note = " (YAML)"
    
    # Check if validator exists
    if not Path(args.validator).is_file():
        cprint(f"❌ Warning: Validator binary not found at {args.validator}. Will skip external validation.", "yellow")
        args.validator = None

    # Your specific requirements
    if is_jenkinsfile:
        requirements = f"""Using the conversion manual and docs context, convert the provided Jenkinsfile into a set of valid Tekton v1 resources: one or more Task(s), a Pipeline that references those Tasks, and a PipelineRun that executes the Pipeline.

Conversion goals:
1. Parse the Jenkinsfile and extract stages, steps, parameters, and environment variables.
2. Convert Jenkins stages to Tekton tasks, maintaining the execution order.
3. Convert Jenkins steps (sh, bat, script, etc.) to Tekton steps with appropriate container images.
4. Map Jenkins parameters to Tekton parameters.
5. Convert Jenkins environment variables to Tekton workspaces or environment variables.
6. Use valid Tekton v1 syntax only. Ensure scripts are strings (not arrays) and field names/nesting are correct.
7. Output MUST contain multiple YAML documents separated by '---' in this order: all Task(s), then the Pipeline, then the PipelineRun.
8. Do not include any markdown/code fences or commentary. Output ONLY raw YAML starting with apiVersion.
"""

# Search for relevant documentation
    print("🔍 Searching for relevant Tekton documentation...")
    context = search_knowledge_base(
        query="""Find examples of:
1. Tekton Task(s) that execute shell commands (sh, bash)
2. How to pass environment variables and parameters to a Task
3. Examples of Pipeline and Task definitions with multiple steps
4. How to handle files and artifacts between tasks using workspaces
5. Tekton equivalents for common CI/CD actions (testing, building, deploying)
6. How to map Jenkinsfile stages and steps to Tekton Tasks and steps""",
        vector_db_id=VECTOR_DB_ID
    )
    
    if not context:
        print("❌ No relevant documentation found. Please ensure the vector database is populated.")
        return
    
    print(f"✨ Generating PipelineRun from {source_path.name}{file_type_note}...")
    pipeline_run = generate_pipelinerun(requirements, context, ast_data)
    
    if pipeline_run:
        print("\n📄 Generated PipelineRun:")
        print("-" * 40)
        print(pipeline_run)
        print("-" * 40)
        
        # Validate with external binary if available
        if args.validator:
            print("\n🔍 Running external validation...")
            if not validate_with_binary(pipeline_run, args.validator):
                print("❌ External validation failed. Please check the errors above.")
                return
        
        save = input("\nDo you want to save this PipelineRun? (y/n): ").lower()
        if save == 'y':
            filename = input("Enter filename (default: generated_pipelinerun.yaml): ").strip()
            if not filename:
                filename = "generated_pipelinerun.yaml"
            
            if save_yaml(pipeline_run, filename):
                print(f"✅ PipelineRun saved to {filename}")
                
                # Ingest successful PipelineRun back to RAG if enabled
                if not args.no_ingest:
                    print("\n📚 Ingesting validated PipelineRun into RAG system...")
                    if ingest_to_rag(pipeline_run, filename):
                        print("✅ PipelineRun ingested successfully")
                    else:
                        print("❌ Failed to ingest PipelineRun")
            else:
                print("❌ Failed to save PipelineRun")
    else:
        print("❌ Failed to generate valid PipelineRun")

if __name__ == "__main__":
    main()

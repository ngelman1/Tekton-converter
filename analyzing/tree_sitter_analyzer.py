#!/usr/bin/env python3
"""
tree_sitter_analyzer.py - Tree-sitter based Jenkinsfile analyzer

This module provides functionality to parse Jenkinsfiles using tree-sitter
and extract pipeline structure, stages, steps, and other relevant information
for conversion to Tekton PipelineRuns.
"""

import os
import json
from typing import Dict, List, Optional, Any
from pathlib import Path
import yaml
from tree_sitter import Language, Parser, Node

class JenkinsfileAnalyzer:
    """Analyzes Jenkinsfiles using tree-sitter to extract pipeline structure."""
    
    def __init__(self, grammar_path: Optional[str] = None):
        """
        Initialize the analyzer with tree-sitter grammar.
        
        Args:
            grammar_path: Path to the tree-sitter grammar library
        """
        self.parser = Parser()
        self.language = None
        self.tree = None
        
        # Try to load the grammar
        if grammar_path and os.path.exists(grammar_path):
            self.language = Language(grammar_path, 'groovy')
            self.parser.set_language(self.language)
        else:
            # Fallback to basic parsing if grammar not available
            print("Warning: Tree-sitter grammar not found. Using basic parsing.")
    
    def parse_jenkinsfile(self, content: str) -> Dict[str, Any]:
        """
        Parse a Jenkinsfile and extract its structure.
        
        Args:
            content: The Jenkinsfile content as string
            
        Returns:
            Dictionary containing the parsed structure
        """
        if not self.language:
            return self._basic_parse(content)
        
        self.tree = self.parser.parse(bytes(content, 'utf8'))
        root_node = self.tree.root_node
        
        return {
            'pipeline_type': self._detect_pipeline_type(root_node),
            'stages': self._extract_stages(root_node),
            'environment': self._extract_environment(root_node),
            'parameters': self._extract_parameters(root_node),
            'tools': self._extract_tools(root_node),
            'post_actions': self._extract_post_actions(root_node),
            'options': self._extract_options(root_node)
        }
    
    def _basic_parse(self, content: str) -> Dict[str, Any]:
        """
        Basic parsing when tree-sitter grammar is not available.
        
        Args:
            content: The Jenkinsfile content
            
        Returns:
            Basic parsed structure
        """
        lines = content.split('\n')
        stages = []
        current_stage = None
        
        for line in lines:
            line = line.strip()
            
            # Detect stage definitions
            if line.startswith('stage(') or line.startswith('stage ('):
                stage_name = self._extract_stage_name(line)
                if stage_name:
                    current_stage = {
                        'name': stage_name,
                        'steps': [],
                        'parallel': False,
                        'when': None
                    }
                    stages.append(current_stage)
            
            # Detect steps within stages
            elif current_stage and line:
                if line.startswith('sh ') or line.startswith('bat '):
                    step = {
                        'type': 'shell',
                        'command': line[3:].strip().strip('"\'')
                    }
                    current_stage['steps'].append(step)
                elif line.startswith('script'):
                    step = {
                        'type': 'script',
                        'content': line
                    }
                    current_stage['steps'].append(step)
        
        return {
            'pipeline_type': 'declarative',  # Assume declarative
            'stages': stages,
            'environment': {},
            'parameters': [],
            'tools': [],
            'post_actions': [],
            'options': {}
        }
    
    def _extract_stage_name(self, line: str) -> Optional[str]:
        """Extract stage name from stage definition line."""
        try:
            # Simple regex-like extraction
            if '(' in line and ')' in line:
                start = line.find('(') + 1
                end = line.find(')')
                name = line[start:end].strip()
                return name.strip('"\'')
        except:
            pass
        return None
    
    def _detect_pipeline_type(self, root_node: Node) -> str:
        """Detect if the pipeline is declarative or scripted."""
        # Look for pipeline keyword
        for node in self._traverse_nodes(root_node):
            if node.type == 'pipeline':
                return 'declarative'
        return 'scripted'
    
    def _extract_stages(self, root_node: Node) -> List[Dict[str, Any]]:
        """Extract stages from the pipeline."""
        stages = []
        
        for node in self._traverse_nodes(root_node):
            if node.type == 'stage':
                stage_info = {
                    'name': self._extract_node_text(node),
                    'steps': self._extract_stage_steps(node),
                    'parallel': self._is_parallel_stage(node),
                    'when': self._extract_when_condition(node)
                }
                stages.append(stage_info)
        
        return stages
    
    def _extract_stage_steps(self, stage_node: Node) -> List[Dict[str, Any]]:
        """Extract steps from a stage."""
        steps = []
        
        for node in self._traverse_nodes(stage_node):
            if node.type in ['sh', 'bat', 'script', 'dir', 'withCredentials']:
                step = {
                    'type': node.type,
                    'content': self._extract_node_text(node),
                    'arguments': self._extract_step_arguments(node)
                }
                steps.append(step)
        
        return steps
    
    def _extract_environment(self, root_node: Node) -> Dict[str, str]:
        """Extract environment variables."""
        env_vars = {}
        
        for node in self._traverse_nodes(root_node):
            if node.type == 'environment':
                for child in node.children:
                    if child.type == 'assignment':
                        key, value = self._extract_assignment(child)
                        if key and value:
                            env_vars[key] = value
        
        return env_vars
    
    def _extract_parameters(self, root_node: Node) -> List[Dict[str, Any]]:
        """Extract pipeline parameters."""
        parameters = []
        
        for node in self._traverse_nodes(root_node):
            if node.type == 'parameters':
                for child in node.children:
                    if child.type in ['string', 'booleanParameter', 'choice']:
                        param = {
                            'type': child.type,
                            'name': self._extract_parameter_name(child),
                            'default': self._extract_parameter_default(child)
                        }
                        parameters.append(param)
        
        return parameters
    
    def _extract_tools(self, root_node: Node) -> List[str]:
        """Extract tools used in the pipeline."""
        tools = []
        
        for node in self._traverse_nodes(root_node):
            if node.type == 'tools':
                for child in node.children:
                    if child.type == 'tool':
                        tool_name = self._extract_node_text(child)
                        if tool_name:
                            tools.append(tool_name)
        
        return tools
    
    def _extract_post_actions(self, root_node: Node) -> List[Dict[str, Any]]:
        """Extract post actions."""
        post_actions = []
        
        for node in self._traverse_nodes(root_node):
            if node.type == 'post':
                for child in node.children:
                    if child.type in ['always', 'success', 'failure', 'unstable']:
                        action = {
                            'condition': child.type,
                            'steps': self._extract_stage_steps(child)
                        }
                        post_actions.append(action)
        
        return post_actions
    
    def _extract_options(self, root_node: Node) -> Dict[str, Any]:
        """Extract pipeline options."""
        options = {}
        
        for node in self._traverse_nodes(root_node):
            if node.type == 'options':
                for child in node.children:
                    if child.type == 'timeout':
                        options['timeout'] = self._extract_timeout_value(child)
                    elif child.type == 'retry':
                        options['retry'] = self._extract_retry_count(child)
        
        return options
    
    def _traverse_nodes(self, node: Node):
        """Traverse all nodes in the tree."""
        yield node
        for child in node.children:
            yield from self._traverse_nodes(child)
    
    def _extract_node_text(self, node: Node) -> str:
        """Extract text content from a node."""
        return node.text.decode('utf-8') if node.text else ''
    
    def _extract_step_arguments(self, step_node: Node) -> Dict[str, Any]:
        """Extract arguments from a step node."""
        args = {}
        
        for child in step_node.children:
            if child.type == 'argument_list':
                for arg in child.children:
                    if arg.type == 'argument':
                        key, value = self._extract_assignment(arg)
                        if key and value:
                            args[key] = value
        
        return args
    
    def _extract_assignment(self, node: Node) -> tuple:
        """Extract key-value assignment from a node."""
        if len(node.children) >= 2:
            key = self._extract_node_text(node.children[0])
            value = self._extract_node_text(node.children[1])
            return key.strip(), value.strip()
        return None, None
    
    def _is_parallel_stage(self, stage_node: Node) -> bool:
        """Check if a stage is parallel."""
        for node in self._traverse_nodes(stage_node):
            if node.type == 'parallel':
                return True
        return False
    
    def _extract_when_condition(self, stage_node: Node) -> Optional[str]:
        """Extract when condition from a stage."""
        for node in self._traverse_nodes(stage_node):
            if node.type == 'when':
                return self._extract_node_text(node)
        return None
    
    def _extract_parameter_name(self, param_node: Node) -> Optional[str]:
        """Extract parameter name."""
        for child in param_node.children:
            if child.type == 'identifier':
                return self._extract_node_text(child)
        return None
    
    def _extract_parameter_default(self, param_node: Node) -> Optional[str]:
        """Extract parameter default value."""
        for child in param_node.children:
            if child.type in ['string_literal', 'boolean_literal']:
                return self._extract_node_text(child)
        return None
    
    def _extract_timeout_value(self, timeout_node: Node) -> Optional[str]:
        """Extract timeout value."""
        return self._extract_node_text(timeout_node)
    
    def _extract_retry_count(self, retry_node: Node) -> Optional[str]:
        """Extract retry count."""
        return self._extract_node_text(retry_node)
    
    def to_tekton_structure(self, parsed_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert parsed Jenkinsfile structure to Tekton-compatible format.
        
        Args:
            parsed_data: The parsed Jenkinsfile data
            
        Returns:
            Tekton-compatible structure
        """
        tekton_structure = {
            'pipeline': {
                'name': 'jenkins-converted-pipeline',
                'params': [],
                'workspaces': [],
                'tasks': []
            },
            'pipelinerun': {
                'name': 'jenkins-converted-pipelinerun',
                'params': [],
                'workspaces': []
            }
        }
        
        # Convert parameters
        for param in parsed_data.get('parameters', []):
            tekton_param = {
                'name': param.get('name', ''),
                'type': self._map_parameter_type(param.get('type', 'string')),
                'description': f'Converted from Jenkins parameter: {param.get("name", "")}'
            }
            tekton_structure['pipeline']['params'].append(tekton_param)
            
            # Add to pipelinerun params if default exists
            if param.get('default'):
                tekton_structure['pipelinerun']['params'].append({
                    'name': param.get('name', ''),
                    'value': param.get('default', '')
                })
        
        # Convert stages to tasks
        for i, stage in enumerate(parsed_data.get('stages', [])):
            task = {
                'name': f"stage-{i+1}-{stage.get('name', 'unnamed').lower().replace(' ', '-')}",
                'runAfter': [] if i == 0 else [f"stage-{i}-{parsed_data['stages'][i-1].get('name', 'unnamed').lower().replace(' ', '-')}"],
                'taskSpec': {
                    'steps': []
                }
            }
            
            # Convert steps
            for j, step in enumerate(stage.get('steps', [])):
                tekton_step = self._convert_step_to_tekton(step, j)
                if tekton_step:
                    task['taskSpec']['steps'].append(tekton_step)
            
            tekton_structure['pipeline']['tasks'].append(task)
        
        return tekton_structure
    
    def _map_parameter_type(self, jenkins_type: str) -> str:
        """Map Jenkins parameter types to Tekton types."""
        type_mapping = {
            'string': 'string',
            'booleanParameter': 'string',  # Tekton doesn't have boolean, use string
            'choice': 'string'
        }
        return type_mapping.get(jenkins_type, 'string')
    
    def _convert_step_to_tekton(self, step: Dict[str, Any], step_index: int) -> Optional[Dict[str, Any]]:
        """Convert a Jenkins step to Tekton step format."""
        step_type = step.get('type', '')
        
        if step_type == 'sh':
            return {
                'name': f'step-{step_index+1}',
                'image': 'alpine:latest',
                'script': step.get('content', '')
            }
        elif step_type == 'bat':
            return {
                'name': f'step-{step_index+1}',
                'image': 'mcr.microsoft.com/windows/servercore:ltsc2019',
                'script': step.get('content', '')
            }
        elif step_type == 'script':
            return {
                'name': f'step-{step_index+1}',
                'image': 'alpine:latest',
                'script': step.get('content', '')
            }
        
        return None

def analyze_jenkinsfile(file_path: str) -> Dict[str, Any]:
    """
    Convenience function to analyze a Jenkinsfile.
    
    Args:
        file_path: Path to the Jenkinsfile
        
    Returns:
        Parsed structure and Tekton conversion
    """
    analyzer = JenkinsfileAnalyzer()
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    parsed_data = analyzer.parse_jenkinsfile(content)
    tekton_structure = analyzer.to_tekton_structure(parsed_data)
    
    return {
        'parsed': parsed_data,
        'tekton': tekton_structure
    }

if __name__ == "__main__":
    # Example usage
    if len(sys.argv) > 1:
        jenkinsfile_path = sys.argv[1]
        result = analyze_jenkinsfile(jenkinsfile_path)
        print(json.dumps(result, indent=2))
    else:
        print("Usage: python tree_sitter_analyzer.py <jenkinsfile_path>") 
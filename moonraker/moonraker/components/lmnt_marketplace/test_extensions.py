"""
LMNT Marketplace Test Extensions Module

This module extends the GCodeManager and JobManager classes with additional methods
needed for comprehensive testing. It patches the classes with methods from
gcode_extensions.py and jobs_extensions.py.

Usage:
    from lmnt_marketplace.test_extensions import apply_test_extensions
    
    # Apply extensions to integration instance
    apply_test_extensions(integration)
"""

import logging
import inspect
import importlib.util
import os
import sys

def apply_test_extensions(integration):
    """
    Apply test extensions to GCodeManager and JobManager
    
    Args:
        integration: LmntMarketplaceIntegration instance
    """
    # Get the directory of this file
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Apply GCode extensions
    gcode_ext_path = os.path.join(current_dir, 'gcode_extensions.py')
    if os.path.exists(gcode_ext_path):
        _apply_extensions(
            integration.gcode_manager,
            gcode_ext_path,
            'gcode_extensions'
        )
    
    # Apply Job extensions
    jobs_ext_path = os.path.join(current_dir, 'jobs_extensions.py')
    if os.path.exists(jobs_ext_path):
        _apply_extensions(
            integration.job_manager,
            jobs_ext_path,
            'jobs_extensions'
        )
    
    logging.info("Applied test extensions to LMNT Marketplace integration")

def _apply_extensions(target_obj, extension_path, module_name):
    """
    Apply extensions from a module to a target object
    
    Args:
        target_obj: Object to extend
        extension_path: Path to extension module
        module_name: Name for the imported module
    """
    try:
        # Import the extension module
        spec = importlib.util.spec_from_file_location(module_name, extension_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # Find all async methods in the module
        for name, func in inspect.getmembers(module, inspect.isfunction):
            if inspect.iscoroutinefunction(func) and not name.startswith('_'):
                # Create a method that calls the function with target_obj as self
                def create_method(func_ref):
                    async def method(*args, **kwargs):
                        return await func_ref(target_obj, *args, **kwargs)
                    return method
                
                # Set the method on the target object
                method = create_method(func)
                method.__name__ = name
                method.__qualname__ = f"{target_obj.__class__.__name__}.{name}"
                setattr(target_obj, name, method)
                logging.info(f"Added method {name} to {target_obj.__class__.__name__}")
    
    except Exception as e:
        logging.error(f"Error applying extensions from {module_name}: {str(e)}")

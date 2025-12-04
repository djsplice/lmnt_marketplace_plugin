"""
LMNT Marketplace Jobs Extensions Module

Additional methods for the Job Manager to support advanced testing:
- Job queue management
- Job status tracking
- Job processing
"""

import os
import json
import logging
import asyncio
import time
from datetime import datetime

async def add_job(self, job):
    """
    Add a job to the print queue
    
    Args:
        job (dict): Job information including ID and priority
        
    Returns:
        bool: True if job was added successfully, False otherwise
    """
    try:
        job_id = job.get('id')
        
        if not job_id:
            logging.error("Cannot add job: Missing job ID")
            return False
        
        # Check if job is already in queue
        if any(j.get('id') == job_id for j in self.print_job_queue):
            logging.warning(f"Job {job_id} is already in queue")
            return True
        
        # Add job to queue
        self.print_job_queue.append(job)
        
        # Sort queue by priority (higher priority first)
        self.print_job_queue.sort(key=lambda j: j.get('priority', 0), reverse=True)
        
        logging.info(f"Added job {job_id} to queue (position {len(self.print_job_queue)})")
        return True
        
    except Exception as e:
        logging.error(f"Error adding job to queue: {str(e)}")
        return False

async def get_next_job(self):
    """
    Get the next job from the queue without removing it
    
    Returns:
        dict: Next job in queue
        None: If queue is empty
    """
    if not self.print_job_queue:
        return None
    
    return self.print_job_queue[0]

async def remove_job(self, job_id):
    """
    Remove a job from the queue
    
    Args:
        job_id (str): ID of job to remove
        
    Returns:
        bool: True if job was removed, False otherwise
    """
    try:
        # Find job in queue
        for i, job in enumerate(self.print_job_queue):
            if job.get('id') == job_id:
                # Remove job
                self.print_job_queue.pop(i)
                logging.info(f"Removed job {job_id} from queue")
                return True
        
        logging.warning(f"Job {job_id} not found in queue")
        return False
        
    except Exception as e:
        logging.error(f"Error removing job from queue: {str(e)}")
        return False

async def update_job_status(self, job_id, status, message=None):
    """
    Update job status locally and in the marketplace
    
    Args:
        job_id (str): Job ID
        status (str): New status ('queued', 'processing', 'printing', 'completed', 'failed', 'cancelled')
        message (str, optional): Status message
        
    Returns:
        bool: True if status update was successful, False otherwise
    """
    try:
        # Store status locally
        if not hasattr(self, 'job_status_map'):
            self.job_status_map = {}
        
        self.job_status_map[job_id] = {
            'status': status,
            'message': message,
            'updated_at': datetime.now().isoformat()
        }
        
        # Update status in marketplace if available
        if hasattr(self, '_update_job_status'):
            try:
                await self._update_job_status(job_id, status, message)
            except Exception as e:
                logging.error(f"Error updating job status in marketplace: {str(e)}")
        
        logging.info(f"Updated job {job_id} status to {status}")
        return True
        
    except Exception as e:
        logging.error(f"Error updating job status: {str(e)}")
        return False

async def get_job_status(self, job_id):
    """
    Get current job status
    
    Args:
        job_id (str): Job ID
        
    Returns:
        str: Current job status
        None: If job not found
    """
    if not hasattr(self, 'job_status_map'):
        return None
    
    job_status = self.job_status_map.get(job_id)
    if job_status:
        return job_status.get('status')
    
    return None

async def process_job(self, job_id):
    """
    Process a job from the queue
    
    Args:
        job_id (str): ID of job to process
        
    Returns:
        bool: True if job was processed successfully, False otherwise
    """
    try:
        # Find job in queue
        job = None
        for j in self.print_job_queue:
            if j.get('id') == job_id:
                job = j
                break
        
        if not job:
            logging.error(f"Job {job_id} not found in queue")
            return False
        
        # Remove job from queue
        await self.remove_job(job_id)
        
        # Update job status
        await self.update_job_status(job_id, 'processing', 'Starting job')
        
        # Set as current job
        self.current_print_job = job
        
        # Simulate job processing
        logging.info(f"Processing job {job_id}")
        await asyncio.sleep(1)
        
        # Update job status to printing
        await self.update_job_status(job_id, 'printing', 'Print started')
        
        # Simulate print completion
        logging.info(f"Simulating print for job {job_id}")
        await asyncio.sleep(2)
        
        # Update job status to completed
        await self.update_job_status(job_id, 'completed', 'Print completed successfully')
        
        # Clear current job
        self.current_print_job = None
        
        logging.info(f"Completed job {job_id}")
        return True
        
    except Exception as e:
        logging.error(f"Error processing job {job_id}: {str(e)}")
        
        # Update job status to failed
        await self.update_job_status(job_id, 'failed', f'Error: {str(e)}')
        
        # Clear current job
        self.current_print_job = None
        
        return False

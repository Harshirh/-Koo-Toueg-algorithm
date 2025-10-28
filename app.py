from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import threading
import time
import random
from collections import defaultdict
import uuid

app = Flask(__name__)
app.config['SECRET_KEY'] = 'koo-toueg-secret'
socketio = SocketIO(app, cors_allowed_origins="*")

class Process:
    def __init__(self, pid, num_processes, custom_name=None):
        self.pid = pid
        self.custom_name = custom_name or f"Node-{pid}"
        self.num_processes = num_processes
        self.state = {
            "temperature": 20.0 + random.uniform(-5, 5),
            "pressure": 1013.0 + random.uniform(-10, 10),
            "wind_speed": 10.0 + random.uniform(-3, 3),
            "humidity": 60.0 + random.uniform(-10, 10),
            "computation_step": 0
        }
        self.checkpoints = []
        self.sent_counter = defaultdict(int)
        self.rcvd_counter = defaultdict(int)
        self.is_failed = False
        self.status = "running"
        self.checkpoint_type = None
        self.messages_sent = 0
        self.messages_received = 0
        
    def take_tentative_checkpoint(self):
        self.status = "checkpointing"
        self.checkpoint_type = "tentative"
        checkpoint = {
            "pid": self.pid,
            "timestamp": time.time(),
            "state": self.state.copy(),
            "sent_counter": dict(self.sent_counter),
            "rcvd_counter": dict(self.rcvd_counter),
            "type": "tentative",
            "checkpoint_id": str(uuid.uuid4())[:8]
        }
        return checkpoint
    
    def commit_checkpoint(self, checkpoint):
        checkpoint["type"] = "permanent"
        self.checkpoints.append(checkpoint)
        self.checkpoint_type = "permanent"
        
    def abort_checkpoint(self):
        self.status = "running"
        self.checkpoint_type = None
        
    def simulate_computation(self, intensity=1.0):
        if not self.is_failed:
            self.state["temperature"] += random.uniform(-0.5, 0.5) * intensity
            self.state["pressure"] += random.uniform(-2, 2) * intensity
            self.state["wind_speed"] += random.uniform(-0.3, 0.3) * intensity
            self.state["humidity"] += random.uniform(-0.5, 0.5) * intensity
            self.state["computation_step"] += 1
            
            # Simulate message passing
            if random.random() > 0.7:
                self.messages_sent += 1
            if random.random() > 0.7:
                self.messages_received += 1
            
    def restore_from_checkpoint(self):
        if self.checkpoints:
            last_checkpoint = self.checkpoints[-1]
            self.state = last_checkpoint["state"].copy()
            self.sent_counter = defaultdict(int, last_checkpoint["sent_counter"])
            self.rcvd_counter = defaultdict(int, last_checkpoint["rcvd_counter"])
            self.is_failed = False
            self.status = "recovering"
            return last_checkpoint["checkpoint_id"]
        return None
        
    def simulate_failure(self):
        self.is_failed = True
        self.status = "failed"
        
    def get_state_dict(self):
        return {
            "pid": self.pid,
            "custom_name": self.custom_name,
            "state": self.state,
            "status": self.status,
            "checkpoint_type": self.checkpoint_type,
            "num_checkpoints": len(self.checkpoints),
            "is_failed": self.is_failed,
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
            "last_checkpoint_id": self.checkpoints[-1]["checkpoint_id"] if self.checkpoints else None
        }

class SimulationController:
    def __init__(self):
        self.processes = []
        self.next_pid = 0
        self.is_running = False
        self.is_paused = False
        self.auto_mode = False
        self.computation_speed = 1.0
        self.checkpoint_frequency = 5
        self.step_count = 0
        
    def add_process(self, custom_name=None):
        process = Process(self.next_pid, len(self.processes) + 1, custom_name)
        self.processes.append(process)
        self.next_pid += 1
        return process.pid
        
    def remove_process(self, pid):
        self.processes = [p for p in self.processes if p.pid != pid]
        
    def get_process(self, pid):
        for p in self.processes:
            if p.pid == pid:
                return p
        return None
        
    def initiate_checkpoint(self):
        socketio.emit('log', {
            'message': '=== CHECKPOINT PHASE 1: Requesting Tentative Checkpoints ===',
            'type': 'checkpoint'
        })
        socketio.sleep(0.3)
        
        tentative_checkpoints = []
        for process in self.processes:
            if not process.is_failed:
                ckpt = process.take_tentative_checkpoint()
                tentative_checkpoints.append((process, ckpt))
                socketio.emit('update', self.get_all_states())
                socketio.emit('log', {
                    'message': f'{process.custom_name}: Tentative checkpoint [{ckpt["checkpoint_id"]}] at step {process.state["computation_step"]}',
                    'type': 'info'
                })
                socketio.sleep(0.2)
        
        socketio.sleep(0.3)
        
        # Phase 2: Commit decision
        all_success = len(tentative_checkpoints) == len([p for p in self.processes if not p.is_failed])
        
        if all_success:
            socketio.emit('log', {
                'message': '=== CHECKPOINT PHASE 2: All ACKs Received - COMMITTING ===',
                'type': 'success'
            })
            socketio.sleep(0.3)
            for process, ckpt in tentative_checkpoints:
                process.commit_checkpoint(ckpt)
                socketio.emit('update', self.get_all_states())
                socketio.emit('log', {
                    'message': f'{process.custom_name}: Checkpoint [{ckpt["checkpoint_id"]}] COMMITTED ✓',
                    'type': 'success'
                })
                socketio.sleep(0.2)
        else:
            socketio.emit('log', {
                'message': '=== CHECKPOINT ABORTED - Some processes failed ===',
                'type': 'error'
            })
            for process, _ in tentative_checkpoints:
                process.abort_checkpoint()
                
        socketio.sleep(0.3)
        for process in self.processes:
            if not process.is_failed:
                process.status = "running"
                process.checkpoint_type = None
                
        socketio.emit('update', self.get_all_states())
        return all_success
        
    def initiate_recovery(self, failed_pid):
        failed_process = self.get_process(failed_pid)
        if not failed_process:
            return
            
        socketio.emit('log', {
            'message': f'=== RECOVERY INITIATED for {failed_process.custom_name} ===',
            'type': 'recovery'
        })
        socketio.sleep(0.5)
        
        failed_process.is_failed = False
        
        for process in self.processes:
            ckpt_id = process.restore_from_checkpoint()
            if ckpt_id:
                socketio.emit('log', {
                    'message': f'{process.custom_name}: Restored to checkpoint [{ckpt_id}] at step {process.state["computation_step"]}',
                    'type': 'recovery'
                })
                socketio.sleep(0.2)
                socketio.emit('update', self.get_all_states())
        
        socketio.sleep(0.5)
        
        for process in self.processes:
            process.status = "running"
            
        socketio.emit('update', self.get_all_states())
        socketio.emit('log', {
            'message': '=== RECOVERY COMPLETE - Resuming Computation ===',
            'type': 'success'
        })
        
    def step_computation(self):
        self.step_count += 1
        socketio.emit('log', {
            'message': f'--- Computation Step {self.step_count} ---',
            'type': 'step'
        })
        
        for process in self.processes:
            process.simulate_computation(self.computation_speed)
            
        socketio.emit('update', self.get_all_states())
        
        # Auto checkpoint based on frequency
        if self.auto_mode and self.step_count % self.checkpoint_frequency == 0:
            socketio.sleep(0.5)
            self.initiate_checkpoint()
        
    def get_all_states(self):
        return {
            'processes': [p.get_state_dict() for p in self.processes],
            'step_count': self.step_count,
            'is_running': self.is_running,
            'is_paused': self.is_paused
        }

# Global controller
controller = SimulationController()

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    emit('update', controller.get_all_states())
    emit('log', {'message': '✓ Connected to Koo-Toueg Simulation Server', 'type': 'info'})

@socketio.on('add_process')
def handle_add_process(data):
    custom_name = data.get('name', None)
    pid = controller.add_process(custom_name)
    emit('update', controller.get_all_states(), broadcast=True)
    emit('log', {
        'message': f'✓ Added new process: {controller.get_process(pid).custom_name}',
        'type': 'info'
    }, broadcast=True)

@socketio.on('remove_process')
def handle_remove_process(data):
    pid = data.get('pid')
    process = controller.get_process(pid)
    if process:
        name = process.custom_name
        controller.remove_process(pid)
        emit('update', controller.get_all_states(), broadcast=True)
        emit('log', {
            'message': f'✗ Removed process: {name}',
            'type': 'error'
        }, broadcast=True)

@socketio.on('trigger_failure')
def handle_trigger_failure(data):
    pid = data.get('pid')
    process = controller.get_process(pid)
    if process and not process.is_failed:
        process.simulate_failure()
        emit('update', controller.get_all_states(), broadcast=True)
        emit('log', {
            'message': f'⚠️  FAILURE: {process.custom_name} has crashed!',
            'type': 'error'
        }, broadcast=True)

@socketio.on('trigger_recovery')
def handle_trigger_recovery(data):
    pid = data.get('pid')
    socketio.start_background_task(controller.initiate_recovery, pid)

@socketio.on('trigger_checkpoint')
def handle_trigger_checkpoint():
    socketio.start_background_task(controller.initiate_checkpoint)

@socketio.on('step_forward')
def handle_step_forward():
    if not controller.is_running or controller.is_paused:
        controller.step_computation()

@socketio.on('start_auto')
def handle_start_auto(data):
    controller.auto_mode = True
    controller.is_running = True
    controller.is_paused = False
    controller.computation_speed = data.get('speed', 1.0)
    controller.checkpoint_frequency = data.get('frequency', 5)
    
    emit('log', {
        'message': f'▶️  AUTO MODE: Starting (Speed: {controller.computation_speed}x, Checkpoint every {controller.checkpoint_frequency} steps)',
        'type': 'info'
    }, broadcast=True)
    
    socketio.start_background_task(auto_simulation_loop)

@socketio.on('pause_simulation')
def handle_pause():
    controller.is_paused = True
    emit('log', {'message': '⏸️  Simulation Paused', 'type': 'info'}, broadcast=True)

@socketio.on('resume_simulation')
def handle_resume():
    controller.is_paused = False
    emit('log', {'message': '▶️  Simulation Resumed', 'type': 'info'}, broadcast=True)

@socketio.on('stop_simulation')
def handle_stop():
    controller.is_running = False
    controller.is_paused = False
    controller.auto_mode = False
    emit('log', {'message': '⏹️  Simulation Stopped', 'type': 'error'}, broadcast=True)

@socketio.on('reset_simulation')
def handle_reset():
    controller.processes = []
    controller.next_pid = 0
    controller.is_running = False
    controller.is_paused = False
    controller.auto_mode = False
    controller.step_count = 0
    emit('update', controller.get_all_states(), broadcast=True)
    emit('log', {'message': '🔄 Simulation Reset', 'type': 'info'}, broadcast=True)

def auto_simulation_loop():
    while controller.is_running and controller.auto_mode:
        if not controller.is_paused:
            controller.step_computation()
            socketio.sleep(1.0 / controller.computation_speed)
        else:
            socketio.sleep(0.1)

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)

cd /var/www/garagesalemap

# Create the master controller
cat > agent_master.py << 'EOF'
"""
Master Controller for Distributed Search Agents
Manages GitHub proxy agents and aggregates results
"""
from flask import Flask, request, jsonify
from datetime import datetime
import uuid
import json

app = Flask(__name__)

# In-memory storage (use Redis in production)
tasks = {}
results = {}
active_proxies = []

@app.route('/api/proxy-results/get-task', methods=['GET'])
def get_task():
    """Proxies poll this to get search tasks"""
    proxy_id = request.args.get('proxy_id')
    
    # Register active proxy
    if proxy_id not in active_proxies:
        active_proxies.append(proxy_id)
        print(f"✓ Proxy registered: {proxy_id}")
    
    # Find pending task for this proxy
    for task_id, task in tasks.items():
        if task['status'] == 'pending' and task.get('assigned_proxy') == proxy_id:
            task['status'] = 'assigned'
            task['assigned_at'] = datetime.utcnow().isoformat()
            return jsonify(task)
    
    # No tasks available
    return jsonify({}), 204

@app.route('/api/proxy-results/submit-results', methods=['POST'])
def submit_results():
    """Proxies POST results here"""
    data = request.json
    task_id = data.get('task_id')
    
    if task_id in tasks:
        tasks[task_id]['status'] = 'completed'
        tasks[task_id]['completed_at'] = datetime.utcnow().isoformat()
        results[task_id] = data
        print(f"✓ Results received: {task_id} - {len(data.get('results', []))} results")
    
    return jsonify({'status': 'ok'})

@app.route('/api/search', methods=['POST'])
def search():
    """Public API - triggers distributed search"""
    data = request.json
    keyword = data.get('keyword')
    location = data.get('location', '')
    
    # Create search task
    task_id = str(uuid.uuid4())
    task = {
        'task_id': task_id,
        'keyword': keyword,
        'location': location,
        'status': 'pending',
        'created_at': datetime.utcnow().isoformat(),
        'assigned_proxy': 'github-proxy-1'  # For now, one proxy
    }
    
    tasks[task_id] = task
    print(f"✓ Task created: {task_id} - {keyword} {location}")
    
    # Wait for results (polling - improve this with websockets later)
    import time
    max_wait = 30  # seconds
    waited = 0
    
    while waited < max_wait:
        if task_id in results:
            return jsonify({
                'task_id': task_id,
                'results': results[task_id]['results'],
                'proxy': results[task_id]['proxy_id'],
                'wait_time': waited
            })
        time.sleep(1)
        waited += 1
    
    return jsonify({
        'error': 'Timeout waiting for results',
        'task_id': task_id
    }), 504

@app.route('/api/status', methods=['GET'])
def status():
    """Check system status"""
    return jsonify({
        'active_proxies': active_proxies,
        'pending_tasks': len([t for t in tasks.values() if t['status'] == 'pending']),
        'completed_tasks': len([t for t in tasks.values() if t['status'] == 'completed']),
        'total_results': sum(len(r.get('results', [])) for r in results.values())
    })

if __name__ == '__main__':
    print("Master Controller starting...")
    print("Waiting for proxy agents to connect...")
    app.run(host='0.0.0.0', port=5001, debug=True)
EOF

echo "✓ Master controller created: agent_master.py"
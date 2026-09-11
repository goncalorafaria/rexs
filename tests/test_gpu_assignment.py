from rexs.compiler import compile_experiment
from rexs.config import SlurmProfile


def test_gpu_steps_are_exclusive_while_cpu_services_share_without_gres():
    spec={'version':'v2','tasks':[
        {'name':'model','image':{'beaker':'runtime'},'command':['true'],'replicas':2,
         'resources':{'gpuCount':1,'cpuCount':2,'memory':'8GiB'}},
        {'name':'evaluation','image':{'beaker':'runtime'},'command':['true'],
         'resources':{'gpuCount':0,'cpuCount':2,'memory':'2GiB'}}]}
    profile=SlurmProfile.from_mapping({'tasks_per_node':3,'completion_task':'evaluation','shared_cpus_per_node':8,'images':{'runtime':'/image.sif'}})
    result=compile_experiment(spec,profile=profile)
    launches=[line for line in result.script.splitlines() if line.strip().startswith('srun ')]
    gpu_steps=[line for line in launches if '--gpus-per-task=1' in line]
    assert result.gpus_per_node == 2
    assert len(gpu_steps) == 2
    for line in gpu_steps:
        assert '--exclusive' in line and '--overlap' not in line
        assert '--cpus-per-task=2' in line
        assert '--gpus-per-node=1' in line
        assert '--gpu-bind=none' not in line
    cpu_steps=[line for line in launches if '--gres=none' in line]
    assert len(cpu_steps) == 1
    assert '--overlap' in cpu_steps[0]
    assert '--cpus-per-task=8' in cpu_steps[0]
    assert 'CUDA_VISIBLE_DEVICES=' not in result.script

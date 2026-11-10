"""Bounded quality controls for the pinned native ComfyUI 3D graphs."""
PRESETS = {
    'preview': dict(geometry_resolution=1024, texture_size=1024, target_faces=150000,
                    remesh_resolution=384, smooth_iterations=20, structure_steps=12,
                    shape_steps=20, refine_steps=12, texture_steps=12,
                    normal_size=1024, ao_size=512, ao_samples=16),
    'balanced': dict(geometry_resolution=1024, texture_size=2048, target_faces=300000,
                     remesh_resolution=768, smooth_iterations=20, structure_steps=12,
                     shape_steps=20, refine_steps=12, texture_steps=12,
                     normal_size=2048, ao_size=1024, ao_samples=32),
    'detail': dict(geometry_resolution=1024, texture_size=4096, target_faces=500000,
                  remesh_resolution=768, smooth_iterations=20, structure_steps=12,
                  shape_steps=20, refine_steps=12, texture_steps=12,
                  normal_size=2048, ao_size=1024, ao_samples=64),
}


def resolve_quality(preset='balanced', overrides=None):
    if preset not in PRESETS:
        raise ValueError('preset must be preview, balanced or detail')
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict) or set(overrides)-set(PRESETS[preset]):
        raise ValueError('Unknown quality parameter')
    settings = {**PRESETS[preset], **overrides}
    choices = {'geometry_resolution': (1024,1536), 'texture_size': (512,1024,2048,4096),
               'normal_size': (512,1024,2048,4096), 'ao_size': (256,512,1024,2048),
               'remesh_resolution': (384,512,768,1024)}
    ranges = {'target_faces': (10000,1000000), 'smooth_iterations': (0,30),
              'structure_steps': (4,50), 'shape_steps': (4,50),
              'refine_steps': (4,50), 'texture_steps': (4,50), 'ao_samples': (8,128)}
    for key,value in settings.items():
        if type(value) is not int:
            raise ValueError(key+' must be an integer')
        if key in choices and value not in choices[key]:
            raise ValueError(key+' outside supported choices')
        if key in ranges and not ranges[key][0] <= value <= ranges[key][1]:
            raise ValueError(key+' outside supported range')
    return settings


def apply_quality(graph, settings):
    """Keep graph topology/conditioning fixed; modify reviewed scalar controls."""
    mapping = {
        'geometry_resolution': ('94','target_resolution'),
        'texture_size': ('288','value'), 'target_faces': ('186','target_face_count'),
        'remesh_resolution': ('241','resolution'), 'smooth_iterations': ('241','smooth_iters'),
        'structure_steps': ('3','steps'), 'shape_steps': ('18','steps'),
        'refine_steps': ('23','steps'), 'texture_steps': ('12','steps'),
        'normal_size': ('224','resolution'), 'ao_size': ('233','resolution'),
        'ao_samples': ('233','samples'),
    }
    for key,(node,field) in mapping.items():
        graph[node]['inputs'][field] = settings[key]
    return graph

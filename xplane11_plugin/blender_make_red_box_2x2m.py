"""
Blender Python Script: Create a 2x2x2 Meter Red Debug Cube for X-Plane 11
How to use in Blender:
1. Open Blender -> Scripting workspace -> Open this file -> Click "Run Script".
2. It creates a 2.0m x 2.0m x 2.0m cube centered at (0, 0, 0) with a bright red material.
3. Note: We have ALSO pre-built `objects/debug_red_box_2x2m.obj` in X-Plane 11 OBJ8 format
   so you can immediately test in X-Plane 11 without needing to manually export from Blender!
"""
import bpy

def create_xp11_red_debug_box_2x2m():
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.object.select_by_type(type='MESH')
    bpy.ops.object.delete()

    # Create 2x2x2 meter cube centered at origin
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 0.0, 0.0))
    cube = bpy.context.active_object
    cube.name = "XP11_Red_Debug_Box_2x2m"

    mat = bpy.data.materials.new(name="XP11_Red_Debug_Material")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.95, 0.12, 0.12, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.4
    cube.data.materials.append(mat)
    print("Created 2x2x2m Red Debug Cube for X-Plane 11!")

if __name__ == "__main__":
    create_xp11_red_debug_box_2x2m()

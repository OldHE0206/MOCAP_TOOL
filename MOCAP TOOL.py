# -*- coding: utf-8 -*-
import maya.cmds as cmds
import math
import os
from PySide2 import QtWidgets, QtCore
from shiboken2 import wrapInstance
import maya.OpenMayaUI as omui
import maya.mel as mel

# ---------- 原始工具函数（部分签名调整以接收参数） ----------
def get_scene_first_last_keyframe():
    """
    获取整个Maya场景时间线上的全局首关键帧和全局末关键帧（纯整数）
    """
    anim_curves = cmds.ls(type='animCurve')
    if not anim_curves:
        print("场景中没有任何关键帧动画！")
        return 0, 0

    all_frames = []
    for curve in anim_curves:
        frames = cmds.keyframe(curve, query=True, timeChange=True)
        if frames:
            all_frames.extend(frames)

    all_frames = sorted(list(set(all_frames)))
    first_frame = int(all_frames[0])
    last_frame = int(all_frames[-1])
    return first_frame, last_frame


def get_world_position(joint, frame):
    cmds.currentTime(frame, edit=True)
    return cmds.xform(joint, query=True, worldSpace=True, translation=True)


def angle_between_vector_and_world_x(v):
    x, y, z = v
    length = math.sqrt(x**2 + y**2 + z**2)
    if length < 0.0001:
        return 0.0
    dot_product = x
    cos_angle = max(min(dot_product / length, 1.0), -1.0)
    return round(math.degrees(math.acos(cos_angle)), 2)


def get_best_uniform_direction_segment(hip, start_frame, end_frame, angle_threshold, speed_tolerance):
    if not cmds.objExists(hip):
        cmds.error(f"错误：未找到骨骼 {hip}！")
        return None

    pos_list = []
    for f in range(start_frame, end_frame + 1):
        px, py, pz = get_world_position(hip, f)
        pos_list.append([f, px, py, pz])

    move_data = []
    for i in range(1, len(pos_list)):
        f0, x0, y0, z0 = pos_list[i-1]
        f1, x1, y1, z1 = pos_list[i]
        vec = [x1-x0, y1-y0, z1-z0]
        dist = math.sqrt(vec[0]**2 + vec[1]**2 + vec[2]**2)
        ang = angle_between_vector_and_world_x(vec)
        move_data.append([f0, f1, dist, ang])

    valid_segments = []
    current_start = None

    for i in range(1, len(move_data)):
        prev_f0, prev_f1, prev_dist, prev_ang = move_data[i-1]
        curr_f0, curr_f1, curr_dist, curr_ang = move_data[i]

        angle_diff = abs(curr_ang - prev_ang)
        same_dir = angle_diff < angle_threshold

        uniform_speed = False
        if prev_dist > 0.0001 and curr_dist > 0.0001:
            ratio = curr_dist / prev_dist
            uniform_speed = (1-speed_tolerance) <= ratio <= (1+speed_tolerance)

        if same_dir and uniform_speed:
            if current_start is None:
                current_start = prev_f0
        else:
            if current_start is not None:
                valid_segments.append([current_start, prev_f1])
                current_start = None

    if current_start is not None and move_data:
        valid_segments.append([current_start, move_data[-1][1]])

    if not valid_segments:
        return None
    valid_segments.sort(key=lambda s: s[1]-s[0], reverse=True)
    return valid_segments[0]


def get_foot_land_frames_in_range(bone_name, min_frame, max_frame, y_threshold):
    if not cmds.objExists(bone_name):
        return []

    valid_frames = []
    for frame in range(min_frame, max_frame + 1):
        cmds.currentTime(frame)
        world_pos = cmds.xform(bone_name, query=True, translation=True, worldSpace=True)
        y_pos = world_pos[1]
        if y_pos < y_threshold:
            valid_frames.append((frame, y_pos))

    result = []
    if valid_frames:
        current_segment = [valid_frames[0]]
        for item in valid_frames[1:]:
            if item[0] == current_segment[-1][0] + 1:
                current_segment.append(item)
            else:
                min_item = min(current_segment, key=lambda x: x[1])
                result.append((bone_name, min_item[0]))
                current_segment = [item]
        min_item = min(current_segment, key=lambda x: x[1])
        result.append((bone_name, min_item[0]))
    return result


def clean_and_shift_keys(keep_start, keep_end):
    all_joints = cmds.ls(type="joint", long=True)
    if not all_joints:
        return

    for jnt in all_joints:
        cmds.cutKey(jnt, time=(-10000, keep_start - 0.1))
        cmds.cutKey(jnt, time=(keep_end + 0.1, 10000))

    offset = -keep_start
    for jnt in all_joints:
        cmds.keyframe(jnt, edit=True, relative=True, timeChange=offset)

    print(f"已整体前移 {keep_start} 帧，动画从 0 开始")
    return offset


def angle_between_vector_and_world_z(vec):
    x, y, z = vec
    length = math.sqrt(x**2 + y**2 + z**2)
    if length < 0.0001:
        return 0.0
    dot_product = z
    cos_angle = max(min(dot_product / length, 1.0), -1.0)
    return round(math.degrees(math.acos(cos_angle)), 2)


def calculate_move_speed(start_pos, end_pos, frame_count, fps):
    dx = end_pos[0] - start_pos[0]
    dy = end_pos[1] - start_pos[1]
    dz = end_pos[2] - start_pos[2]
    distance = math.sqrt(dx**2 + dy**2 + dz**2)
    total_time = frame_count / fps
    if total_time <= 0:
        return 0.0
    return round(distance / total_time, 3)


def set_lock_attr(obj, attr, lock=False):
    cmds.setAttr(f"{obj}.{attr}", lock=lock)


def reset_hips_translation(hip):
    """清除指定骨骼的 X/Z 位移"""
    if not cmds.objExists(hip):
        cmds.warning(f"未找到骨骼：{hip}")
        return
    set_lock_attr(hip, "translateX")
    set_lock_attr(hip, "translateZ")
    cmds.cutKey(hip, attribute=("translateX", "translateZ"), clear=True)
    cmds.setAttr(f"{hip}.translateX", 0)
    cmds.setAttr(f"{hip}.translateZ", 0)


def add_rotation_to_keyframes(hip, attribute, add_value):
    attr_path = f"{hip}.{attribute}"
    if not cmds.objExists(hip):
        cmds.warning(f"错误：找不到骨骼 {hip}")
        return False
    if not cmds.attributeQuery(attribute, node=hip, exists=True):
        cmds.warning(f"错误：{hip} 没有 {attribute} 属性")
        return False

    key_frames = cmds.keyframe(attr_path, query=True, time=())
    if not key_frames:
        cmds.warning(f"{hip}.{attribute} 没有关键帧")
        return False

    for frame in key_frames:
        original_val = cmds.getAttr(attr_path, time=frame)
        new_val = original_val + add_value
        cmds.keyframe(attr_path, time=(frame, frame), valueChange=new_val)
    return True


def set_first_last_to_average():
    """将第一帧与倒数第三帧的平均值同时赋予第一帧和最后一帧，实现动画平滑循环"""
    all_joints = cmds.ls(type='joint', long=True)
    if not all_joints:
        print("未找到骨骼，无法同步")
        return

    fStart, fEnd = get_scene_first_last_keyframe()
    if fEnd - 2 < fStart:
        print("动画过短，无法使用倒数第三帧进行混合")
        return

    print(f"正在同步：第{fStart}帧 --- 第{fEnd}帧 - 平均值（参考倒数第3帧）")
    for jnt in all_joints:
        cmds.currentTime(fStart)
        t1_x = cmds.getAttr(f"{jnt}.translateX")
        t1_y = cmds.getAttr(f"{jnt}.translateY")
        t1_z = cmds.getAttr(f"{jnt}.translateZ")
        r1_x = cmds.getAttr(f"{jnt}.rotateX")
        r1_y = cmds.getAttr(f"{jnt}.rotateY")
        r1_z = cmds.getAttr(f"{jnt}.rotateZ")

        cmds.currentTime(fEnd - 2)
        t2_x = cmds.getAttr(f"{jnt}.translateX")
        t2_y = cmds.getAttr(f"{jnt}.translateY")
        t2_z = cmds.getAttr(f"{jnt}.translateZ")
        r2_x = cmds.getAttr(f"{jnt}.rotateX")
        r2_y = cmds.getAttr(f"{jnt}.rotateY")
        r2_z = cmds.getAttr(f"{jnt}.rotateZ")

        avg_t_x = (t1_x + t2_x) / 2
        avg_t_y = (t1_y + t2_y) / 2
        avg_t_z = (t1_z + t2_z) / 2
        avg_r_x = (r1_x + r2_x) / 2
        avg_r_y = (r1_y + r2_y) / 2
        avg_r_z = (r1_z + r2_z) / 2

        cmds.currentTime(fStart)
        cmds.setKeyframe(jnt, attribute='translateX', value=avg_t_x)
        cmds.setKeyframe(jnt, attribute='translateY', value=avg_t_y)
        cmds.setKeyframe(jnt, attribute='translateZ', value=avg_t_z)
        cmds.setKeyframe(jnt, attribute='rotateX', value=avg_r_x)
        cmds.setKeyframe(jnt, attribute='rotateY', value=avg_r_y)
        cmds.setKeyframe(jnt, attribute='rotateZ', value=avg_r_z)

        cmds.currentTime(fEnd)
        cmds.setKeyframe(jnt, attribute='translateX', value=avg_t_x)
        cmds.setKeyframe(jnt, attribute='translateY', value=avg_t_y)
        cmds.setKeyframe(jnt, attribute='translateZ', value=avg_t_z)
        cmds.setKeyframe(jnt, attribute='rotateX', value=avg_r_x)
        cmds.setKeyframe(jnt, attribute='rotateY', value=avg_r_y)
        cmds.setKeyframe(jnt, attribute='rotateZ', value=avg_r_z)

    print(" 首尾帧已统一为平均值")


# ---------- 核心执行逻辑 ----------
def execute_script(hip_joint, foot_bones, attribute, y_threshold,
                   angle_threshold, speed_tolerance, fps):
    """使用指定参数执行动画提取与矫正流程"""
    first_frame, last_frame = get_scene_first_last_keyframe()
    if first_frame == last_frame:
        cmds.warning("场景时间范围内没有足够的关键帧")
        return

    start_frame = first_frame
    end_frame = last_frame

    best_segment = get_best_uniform_direction_segment(
        hip_joint, start_frame, end_frame, angle_threshold, speed_tolerance
    )
    if not best_segment:
        print("未检测到有效匀速同向区间")
        return

    best_s, best_e = best_segment

    all_lands = []
    for foot in foot_bones:
        lands = get_foot_land_frames_in_range(foot, best_s, best_e, y_threshold)
        all_lands.extend(lands)
    all_lands.sort(key=lambda x: x[1])

    all_valid_sequences = []
    for i in range(len(all_lands) - 2):
        all_valid_sequences.append(all_lands[i:i+3])

    final_sequence = None
    if all_valid_sequences:
        mid_index = len(all_valid_sequences) // 2
        final_sequence = all_valid_sequences[mid_index]

    if not final_sequence:
        print("未找到连续3步落地动作")
        return

    frames = [s[1] for s in final_sequence]
    gait_start = frames[0]
    gait_end = frames[-1]
    print(f"3个关键帧整体范围：【 {gait_start} ～ {gait_end} 】帧")

    clean_and_shift_keys(gait_start, gait_end)

    new_start_frame = 0
    new_end_frame = gait_end - gait_start
    total_frames = new_end_frame - new_start_frame

    hip_start_pos = get_world_position(hip_joint, new_start_frame)
    hip_end_pos = get_world_position(hip_joint, new_end_frame)

    move_vec = [hip_end_pos[0] - hip_start_pos[0],
                hip_end_pos[1] - hip_start_pos[1],
                hip_end_pos[2] - hip_start_pos[2]]

    z_angle = angle_between_vector_and_world_z(move_vec)
    speed = int(calculate_move_speed(hip_start_pos, hip_end_pos, total_frames, fps))
    print(f"运动速度：{speed/100} m/s")

    if add_rotation_to_keyframes(hip_joint, attribute, float(z_angle)):
        print(f"已为 {hip_joint}.{attribute} 添加 {z_angle:.2f}° 补偿")

    reset_hips_translation(hip_joint)
    set_first_last_to_average()
    print("========== 脚本执行完毕 ==========")


# ---------- PySide2 UI ----------
class AnimCycleUI(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(AnimCycleUI, self).__init__(parent)
        self.setWindowTitle("动画循环提取工具")
        self.setMinimumWidth(500)
        self.setup_ui()
        self.set_defaults()

    def setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(8)

        # ========== 文件地址区域 ==========
        file_layout = QtWidgets.QHBoxLayout()
        file_label = QtWidgets.QLabel("File Address:")
        self.file_line_edit = QtWidgets.QLineEdit()
        self.file_line_edit.setPlaceholderText("选择或输入 FBX 文件路径...")
        self.browse_btn = QtWidgets.QPushButton("Browse")
        self.browse_btn.clicked.connect(self.on_browse)
        file_layout.addWidget(file_label)
        file_layout.addWidget(self.file_line_edit)
        file_layout.addWidget(self.browse_btn)
        main_layout.addLayout(file_layout)

        # 水平分隔线
        line1 = QtWidgets.QFrame()
        line1.setFrameShape(QtWidgets.QFrame.HLine)
        line1.setFrameShadow(QtWidgets.QFrame.Sunken)
        main_layout.addWidget(line1)

        # ========== 原有参数区域 ==========
        hip_label = QtWidgets.QLabel("髋部骨骼 (Hips):")
        self.hip_line = QtWidgets.QLineEdit()
        main_layout.addWidget(hip_label)
        main_layout.addWidget(self.hip_line)

        foot_label = QtWidgets.QLabel("脚部骨骼 (逗号分隔):")
        self.foot_line = QtWidgets.QLineEdit()
        main_layout.addWidget(foot_label)
        main_layout.addWidget(self.foot_line)

        attr_label = QtWidgets.QLabel("补偿旋转属性:")
        self.attr_combo = QtWidgets.QComboBox()
        self.attr_combo.addItems(["rotateY", "rotateX", "rotateZ"])
        self.attr_combo.setEditable(True)
        main_layout.addWidget(attr_label)
        main_layout.addWidget(self.attr_combo)

        y_label = QtWidgets.QLabel("脚部落地 Y 阈值:")
        self.y_spin = QtWidgets.QDoubleSpinBox()
        self.y_spin.setRange(0.0, 1000.0)
        self.y_spin.setDecimals(2)
        self.y_spin.setSingleStep(1.0)
        main_layout.addWidget(y_label)
        main_layout.addWidget(self.y_spin)

        angle_label = QtWidgets.QLabel("方向一致角度阈值 (度):")
        self.angle_spin = QtWidgets.QDoubleSpinBox()
        self.angle_spin.setRange(0.0, 180.0)
        self.angle_spin.setDecimals(2)
        self.angle_spin.setSingleStep(1.0)
        main_layout.addWidget(angle_label)
        main_layout.addWidget(self.angle_spin)

        speed_label = QtWidgets.QLabel("速度容差 (0~1):")
        self.speed_spin = QtWidgets.QDoubleSpinBox()
        self.speed_spin.setRange(0.0, 1.0)
        self.speed_spin.setDecimals(3)
        self.speed_spin.setSingleStep(0.05)
        main_layout.addWidget(speed_label)
        main_layout.addWidget(self.speed_spin)

        fps_label = QtWidgets.QLabel("帧率 (FPS):")
        self.fps_spin = QtWidgets.QSpinBox()
        self.fps_spin.setRange(1, 120)
        self.fps_spin.setValue(30)
        main_layout.addWidget(fps_label)
        main_layout.addWidget(self.fps_spin)

        # 窗口置顶勾选框
        self.top_checkbox = QtWidgets.QCheckBox("窗口置顶")
        self.top_checkbox.setChecked(False)
        self.top_checkbox.stateChanged.connect(self.toggle_stay_on_top)
        main_layout.addWidget(self.top_checkbox)

        # 第二道水平分隔线
        line2 = QtWidgets.QFrame()
        line2.setFrameShape(QtWidgets.QFrame.HLine)
        line2.setFrameShadow(QtWidgets.QFrame.Sunken)
        main_layout.addWidget(line2)

        # ========== 底部按钮行 ==========
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(10)

        self.import_btn = QtWidgets.QPushButton("Import")
        self.import_btn.clicked.connect(self.on_import)
        self.export_btn = QtWidgets.QPushButton("Export")
        self.export_btn.clicked.connect(self.on_export)

        button_layout.addWidget(self.import_btn)
        button_layout.addWidget(self.export_btn)
        button_layout.addStretch()

        self.run_button = QtWidgets.QPushButton("执行")
        self.run_button.clicked.connect(self.on_run)
        self.close_button = QtWidgets.QPushButton("关闭")
        self.close_button.clicked.connect(self.close)

        button_layout.addWidget(self.run_button)
        button_layout.addWidget(self.close_button)

        main_layout.addLayout(button_layout)

    def toggle_stay_on_top(self, state):
        if state == QtCore.Qt.Checked:
            self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowStaysOnTopHint)
        self.show()

    def set_defaults(self):
        self.hip_line.setText("Hips")
        self.foot_line.setText("RightFoot,LeftFoot")
        self.attr_combo.setCurrentText("rotateY")
        self.y_spin.setValue(18.0)
        self.angle_spin.setValue(30.0)
        self.speed_spin.setValue(0.2)
        self.fps_spin.setValue(30)

    # ----- 文件地址相关槽函数 -----
    def on_browse(self):
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择 FBX 文件", "", "FBX 文件 (*.fbx);;所有文件 (*.*)"
        )
        if file_path:
            self.file_line_edit.setText(file_path)

    def on_import(self):
        file_path = self.file_line_edit.text().strip()
        if not file_path:
            QtWidgets.QMessageBox.warning(self, "路径错误", "请先选择或输入 FBX 文件路径")
            return
        if not os.path.exists(file_path):
            QtWidgets.QMessageBox.warning(self, "文件不存在", f"找不到文件：{file_path}")
            return
        try:
            cmds.loadPlugin('fbxmaya.mll', quiet=True)
            # 导入FBX 不使用命名空间
            imported_nodes = cmds.file(
                file_path,
                i=True,
                type="FBX",
                ignoreVersion=True,
                options="v=0;",
                returnNewNodes=True
            )
            
            # 强制删除所有命名空间（修复Maya2024不生效问题）
            all_namespaces = cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True)
            for ns in all_namespaces:
                if ns not in ['UI', 'shared']:
                    try:
                        cmds.namespace(removeNamespace=ns, mergeNamespaceWithRoot=True)
                    except:
                        pass
            
            QtWidgets.QMessageBox.information(self, "导入成功", f"已导入并清除命名空间：{file_path}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "导入失败", str(e))
            raise

    def on_export(self):
        # 获取源文件路径作为默认目录和基础名
        src_path = self.file_line_edit.text().strip()
        if src_path and os.path.isfile(src_path):
            default_dir = os.path.dirname(src_path)
            base_name = os.path.splitext(os.path.basename(src_path))[0]
        else:
            # 如果源路径无效，使用当前工作区或用户目录
            default_dir = cmds.workspace(query=True, rootDirectory=True) or os.path.expanduser("~")
            base_name = "animation"

        # 弹出另存为对话框，让用户自由选择保存位置和文件名
        suggested_name = f"{base_name}_Ani.fbx"
        export_path, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出 FBX 动画",
            os.path.join(default_dir, suggested_name),
            "FBX 文件 (*.fbx)"
        )

        if not export_path:
            return  # 用户取消了

        # 确保扩展名正确
        if not export_path.lower().endswith('.fbx'):
            export_path += '.fbx'

        first, last = get_scene_first_last_keyframe()
        if first == last:
            QtWidgets.QMessageBox.warning(self, "无动画", "场景中没有关键帧动画，无法导出")
            return

        try:
            cmds.loadPlugin('fbxmaya.mll', quiet=True)
            # 兼容Maya2024的FBX导出设置（删除了废弃命令）
            mel.eval('FBXResetExport')
            mel.eval('FBXExportInAscii -v false')
            mel.eval('FBXExportFileVersion "FBX202000"')
            mel.eval('FBXExportBakeComplexAnimation -v true')
            mel.eval(f'FBXExportBakeComplexStart -v {first}')
            mel.eval(f'FBXExportBakeComplexEnd -v {last}')
            mel.eval('FBXExportApplyConstantKeyReducer -v false')
            mel.eval('FBXExportSkins -v true')
            mel.eval('FBXExportConstraints -v true')
            mel.eval('FBXExportSkeletonDefinitions -v true')

            # 执行导出
            mel.eval(f'FBXExport -f "{export_path}"')
            QtWidgets.QMessageBox.information(self, "导出成功", f"已导出至：{export_path}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "导出失败", str(e))
            raise

    # ----- 执行按钮 -----
    def on_run(self):
        hip = self.hip_line.text().strip()
        if not hip:
            QtWidgets.QMessageBox.warning(self, "输入错误", "请输入髋部骨骼名称")
            return
        if not cmds.objExists(hip):
            QtWidgets.QMessageBox.warning(self, "骨骼不存在", f"场景中找不到骨骼: {hip}")
            return

        foot_text = self.foot_line.text().strip()
        if not foot_text:
            QtWidgets.QMessageBox.warning(self, "输入错误", "请输入至少一个脚部骨骼名称")
            return
        foot_bones = [name.strip() for name in foot_text.split(",") if name.strip()]
        for foot in foot_bones:
            if not cmds.objExists(foot):
                QtWidgets.QMessageBox.warning(self, "骨骼不存在", f"场景中找不到骨骼: {foot}")
                return

        attribute = self.attr_combo.currentText().strip()
        if not attribute:
            QtWidgets.QMessageBox.warning(self, "输入错误", "请选择或输入旋转属性")
            return

        y_threshold = self.y_spin.value()
        angle_threshold = self.angle_spin.value()
        speed_tolerance = self.speed_spin.value()
        fps = self.fps_spin.value()

        try:
            execute_script(hip, foot_bones, attribute, y_threshold,
                           angle_threshold, speed_tolerance, fps)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "执行出错", str(e))
            raise

        QtWidgets.QMessageBox.information(self, "完成", "动画循环处理已完成！")


def get_maya_main_window():
    main_window_ptr = omui.MQtUtil.mainWindow()
    if main_window_ptr is not None:
        return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
    return None


if __name__ == "__main__":
    try:
        # 关闭已存在的窗口，避免重复
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if widget.windowTitle() == "动画循环提取工具":
                widget.close()
        ui = AnimCycleUI(parent=get_maya_main_window())
        ui.show()
    except:
        ui = AnimCycleUI()
        ui.show()

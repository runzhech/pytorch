#!/usr/bin/env python3
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Dict, List

import torch
import yaml
from tools.codegen.code_template import CodeTemplate
from torch.jit.operator_upgraders import generate_bytecode


class ByteCode(Enum):
    instructions = 1
    constants = 2
    types = 3
    operators = 4
    register_size = 5

def load_yaml(upgrader_yaml_path: str) -> Dict:
    with open(upgrader_yaml_path, "rb") as yaml_file:
        return yaml.safe_load(yaml_file)


ONE_INSTRUCTION = CodeTemplate("""
    Instruction{OpCode::${operator_name}, ${X}, ${N}},""")

INSTRUCTION_LIST = CodeTemplate("""std::vector<Instruction>({
        ${instruction_list}
    }), // instructions list""")

ONE_CONSTANT = CodeTemplate("""c10::IValue(${constant}),""")

CONSTANT_LIST = CodeTemplate("""std::vector<c10::IValue>({
        ${constant_list}
    }), // constants list""")

ONE_TYPE = CodeTemplate("""c10::parseType("${type_str}"),""")

TYPE_LIST = CodeTemplate("""std::vector<c10::TypePtr>({
        ${type_list}
    }), // types list""")

ONE_OPERATOTR_STRING = CodeTemplate("""
    OperatorString({"${operator_name}", "${overload_name}", ${num_of_args}}),""")

OPERATOR_STRING_LIST = CodeTemplate("""
    std::vector<OperatorString>({
        ${operator_string_list}
    }), // operators list""")

ONE_UPGRADER_FUNCTION = CodeTemplate("""
    mobile::Function::registerFunc(
        "${upgrader_name}",
        ${instruction_list},
        ${constant_list},
        ${type_list},
        ${register_size}
    )""")

ONE_UPGRADER_SRC = CodeTemplate("""
    ByteCodeFunctionWithOperator({
        ${bytecode_function},
        ${operator_string_list}
    }),""")


ONE_UPGRADER_IN_VERSION_MAP = CodeTemplate("""Upgrader({${upgrader_min_version}, ${upgrader_max_version}, "${upgrader_name}", ${bytecode_func_index}})""")

ONE_OPERATOR_IN_VERSION_MAP = CodeTemplate("""
    {std::string("${operator_name}"),
        std::vector<Upgrader>({
            ${upgrader_list_in_version_map}
        })},""")

# OPERATOR_VERSION_MAP = CodeTemplate("""
# const std::unordered_map<std::string, std::vector<Upgrader>> kOperatorVersionMap(
#     {
#         ${operator_list_in_version_map}
#     });
# """)

OPERATOR_VERSION_MAP = CodeTemplate("""
const std::unordered_map<std::string, std::vector<Upgrader>>
getOperatorVersionMapForMobile() {
  static std::unordered_map<std::string, std::vector<Upgrader>>
        operatorVersionMapForMobile({
            ${operator_list_in_version_map}
      });
  return operatorVersionMapForMobile;
}
""")



UPGRADER_CPP_SRC = CodeTemplate("""/**
 * @generated
 * This is an auto-generated file. Please do not modify it by hand.
 * To re-generate, please run:
 * cd ~/pytorch && python torch/csrc/jit/mobile/upgrader_mobile.cpp
 */

#include <torch/csrc/jit/mobile/upgrader_mobile.h>
#include <ATen/core/ivalue.h>

namespace c10 {
TypePtr parseType(const std::string& pythonStr);
} // namespace c10

namespace torch {
namespace jit {

// From operator_versions_map
${operator_version_map}

std::vector<ByteCodeFunctionWithOperator> getUpgraderBytecodeList() {
  static std::vector<ByteCodeFunctionWithOperator> upgraderBytecodeList({
       ${upgrader_bytecode}
  });
  return upgraderBytecodeList;
}

} // namespace jit
} // namespace torch

""")

UPGRADER_MOBILE_FILE_NAME = "upgrader_mobile.cpp"

UPGRADER_ELEMENT = CodeTemplate("""\
Upgrader({${min_version}, ${max_version}, ${operator_name}, ${index}}),
""")

PER_OPERATOR_UPGRADER_LIST = CodeTemplate("""\
{
  std::string(${operator_name}),
  std::vector<Upgrader>({${upgrader_list}});
}
""")

def construct_instruction(instruction_list_from_yaml: List) -> str:
    instruction_list_part = []
    for instruction in instruction_list_from_yaml:
        instruction_list_part.append(
            ONE_INSTRUCTION.substitute(
                operator_name=instruction[0],
                X=instruction[1],
                N=instruction[2],
            )
        )
    return INSTRUCTION_LIST.substitute(instruction_list="".join(instruction_list_part))

def construct_constants(constants_list_from_yaml: List) -> str:
    constants_list_part = []
    for constant_from_yaml in constants_list_from_yaml:
        convert_constant = None
        if isinstance(constant_from_yaml, str):
            # Add quotes if it's string
            convert_constant = f'"{constant_from_yaml}"'
        elif isinstance(constant_from_yaml, bool):
            convert_constant = "true" if constant_from_yaml else "false"
        elif constant_from_yaml is None:
            convert_constant = ""
        else:
            raise ValueError(
                f"The type of {constant_from_yaml} is {type(constant_from_yaml)}. "
                "Please add change in construct_constants function in gen_mobile_upgraders.py.")
        constants_list_part.append(
            ONE_CONSTANT.substitute(
                constant=convert_constant
            )
        )
    return CONSTANT_LIST.substitute(constant_list="".join(constants_list_part))

def construct_operators(operator_list_from_yaml: List) -> str:
    operator_list_part = []
    for operator in operator_list_from_yaml:
        operator_list_part.append(
            ONE_OPERATOTR_STRING.substitute(
                operator_name=operator[0],
                overload_name=operator[1],
                num_of_args=operator[2],
            )
        )
    return OPERATOR_STRING_LIST.substitute(operator_string_list="".join(operator_list_part))

def construct_types(types_tr_list_from_yaml: List) -> str:
    types_tr_list_part = []
    for types_tr in types_tr_list_from_yaml:
        types_tr_list_part.append(
            ONE_TYPE.substitute(
                type_str=types_tr
            )
        )
    return TYPE_LIST.substitute(type_list="".join(types_tr_list_part))

def construct_register_size(register_size_from_yaml: int) -> str:
    if (not isinstance(register_size_from_yaml, int)):
        raise ValueError(
            f"Input register size is {register_size_from_yaml} and"
            "it's type is {type(register_size_from_yaml)}. An int type is expected.")
    return str(register_size_from_yaml)

def construct_one_operator_in_version_map(operator_name: str, upgrader_list: List) -> str:
    upgraders_in_version_map_part = []
    for one_upgrader in upgrader_list:
        upgraders_in_version_map_part.append(
            ONE_UPGRADER_IN_VERSION_MAP.substitute(
                upgrader_min_version=one_upgrader[0],
                upgrader_max_version=one_upgrader[1],
                upgrader_name=one_upgrader[2],
                bytecode_func_index=one_upgrader[3]
            )
        )
    return ONE_OPERATOR_IN_VERSION_MAP.substitute(
        operator_name=operator_name,
        upgrader_list_in_version_map="".join(upgraders_in_version_map_part)
    )

def construct_version_maps(upgrader_bytecode_function_to_index_map: Dict) -> Dict:
    version_map = torch._C._get_operator_version_map()
    sorted_version_map = {
        op_name: sorted(upgrader_entry_list, key=lambda upgrader_entry: upgrader_entry.upgrader_name) for op_name, upgrader_entry_list in sorted(version_map.items(), key=lambda item: item[0])
        }

    operator_list_in_version_map_part = []
    for op_name in sorted_version_map:
        upgraders_in_version_map_part = []
        # TODO: remove the skip after these two operators schemas are fixed
        if op_name == "aten::full.names" or op_name == "aten::full.out":
            continue
        for upgrader_entry in sorted_version_map[op_name]:
            # Split a string by "_" and filter empty string in the list
            # For example: "div__Scalar_0_3" => ['div', 'Scalar', '0', '3']
            upgrader_info = list(filter(lambda token: token != "", upgrader_entry.upgrader_name.split('_')))
            upgrader_min_version = upgrader_info[2]
            upgrader_max_version = upgrader_info[3]
            upgrader_name = upgrader_entry.upgrader_name

            bytecode_function_index = upgrader_bytecode_function_to_index_map[upgrader_name]
            upgraders_in_version_map_part.append(
                ONE_UPGRADER_IN_VERSION_MAP.substitute(
                    upgrader_min_version=upgrader_min_version,
                    upgrader_max_version=upgrader_max_version,
                    upgrader_name=upgrader_name,
                    bytecode_func_index=bytecode_function_index,
                )
            )
        operator_list_in_version_map_part.append(
            ONE_OPERATOR_IN_VERSION_MAP.substitute(
                operator_name=op_name,
                upgrader_list_in_version_map="".join(upgraders_in_version_map_part)
            )
        )
    return OPERATOR_VERSION_MAP.substitute(
        operator_list_in_version_map="".join(operator_list_in_version_map_part)
    )

def get_upgrader_bytecode_function_to_index_map(upgrader_dict: Dict) -> Dict:
    upgrader_bytecode_function_to_index_map = {}
    index = 0
    for upgrader_bytecode in upgrader_dict:
        for upgrader_name, bytecode in upgrader_bytecode.items():
            upgrader_bytecode_function_to_index_map[upgrader_name] = index
            index += 1
    return upgrader_bytecode_function_to_index_map

def write_cpp(cpp_path: str, upgrader_dict: List):
    body_parts = []
    upgrader_bytecode_function_to_index_map = get_upgrader_bytecode_function_to_index_map(upgrader_dict)
    version_map_src = construct_version_maps(upgrader_bytecode_function_to_index_map)
    all_upgrader_src_string = []
    for upgrader_bytecode in upgrader_dict:
        for upgrader_name, bytecode in upgrader_bytecode.items():
            # TODO: remove the skip after these two operators schemas are fixed
            if upgrader_name == "full_names_0_4" or upgrader_name == "full_out_0_4":
                continue
            instruction_list_str = ""
            constant_list_str = ""
            type_list_str = ""
            register_size_str = ""
            operator_list_str = ""
            for table_name, contents in bytecode.items():
                element = ByteCode[table_name]
                body_string = ""
                if element is ByteCode.instructions:
                    instruction_list_str = construct_instruction(contents)
                elif element is ByteCode.constants:
                    constant_list_str = construct_constants(contents)
                elif element is ByteCode.operators:
                    operator_list_str = construct_operators(contents)
                elif element is ByteCode.types:
                    type_list_str = construct_types(contents)
                elif element is ByteCode.register_size:
                    register_size_str = construct_register_size(contents)

            one_upgrader_function_string = ONE_UPGRADER_FUNCTION.substitute(
                upgrader_name=upgrader_name,
                instruction_list=instruction_list_str,
                constant_list=constant_list_str,
                type_list=type_list_str,
                register_size=register_size_str,
            )
            one_upgrader_src_string = ONE_UPGRADER_SRC.substitute(
                bytecode_function=one_upgrader_function_string,
                operator_string_list=operator_list_str,
            )
            all_upgrader_src_string.append(one_upgrader_src_string)

    upgrader_file_content = UPGRADER_CPP_SRC.substitute(
        operator_version_map=version_map_src,
        upgrader_bytecode="".join(all_upgrader_src_string))
    body_parts.append(upgrader_file_content)
    print("writing file to : ", cpp_path + "/" + UPGRADER_MOBILE_FILE_NAME)
    with open(
        os.path.join(cpp_path, UPGRADER_MOBILE_FILE_NAME), "wb"
    ) as out_file:
        final_output = "".join(body_parts)
        out_file.write(upgrader_file_content.encode("utf-8"))

def sort_upgrader(upgrader_list: List) -> List:
    sorted_upgrader_list = sorted(upgrader_list, key = lambda one_upgrader: next(iter(one_upgrader)))
    return sorted_upgrader_list

def main():

    upgrader_list = generate_bytecode()
    sorted_upgrader_list = sort_upgrader(upgrader_list)
    for up in sorted_upgrader_list:
        print("after sort upgrader : ", next(iter(up)))

    pytorch_dir = Path(__file__).resolve().parents[3]
    upgrader_path = pytorch_dir / "torch" / "csrc" / "jit" / "mobile"
    write_cpp(str(upgrader_path), sorted_upgrader_list)

if __name__ == '__main__':
    main()

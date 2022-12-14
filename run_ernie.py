from processors.TryDataProcessor import TryDataProcessor
from transformers import BertTokenizer
from models.bert import Bert, BertSentence
from sklearn import metrics
import json
import pandas as pd

from utils.k_fold import cross_validation
from utils.utils import *
from utils.train_eval import *

MODEL_CLASSES = {
   'bert':  Bert,
   'bert_sentence': BertSentence,
   'multi_bert': Bert,  # multi_loss_tag
}


class NewsConfig:

    def __init__(self):
        absdir = os.path.dirname(os.path.abspath(__file__))
        _pretrain_path = '/pretrain_models/ERNIE'  
        _config_file = 'bert_config.json' 
        _model_file = 'pytorch_model.bin'
        _tokenizer_file = 'vocab.txt'
        _data_path = '/real_data'

        # 使用的模型
        self.use_model = 'bert'

        self.models_name = 'ernie'
        self.task = 'base_real_data'
        self.config_file =[os.path.join(absdir + _pretrain_path, _config_file)]
        self.model_name_or_path = [os.path.join(absdir + _pretrain_path, _model_file)]
        self.tokenizer_file = os.path.join(absdir + _pretrain_path, _tokenizer_file)
        self.data_dir = absdir + _data_path
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')              # 设备
        self.device_id = 0
        self.do_lower_case = True
        self.label_on_test_set = True
        self.requires_grad = True
        self.class_list = []
        self.num_labels = 2
        self.train_num_examples = 0
        self.dev_num_examples = 0
        self.test_num_examples = 0
        self.hidden_dropout_prob = 0.1
        self.hidden_size = [768]
        self.early_stop = False
        self.require_improvement = 800 if self.use_model == 'bert' else 5000                    # 若超过1000batch效果还没提升，则提前结束训练
        self.num_train_epochs = 5                                                              # epoch数
        self.batch_size = 64                                                                      # mini-batch大小
        self.pad_size = 64                                                                      # 每句话处理成的长度
        self.learning_rate = 2e-5                                                               # 学习率
        self.head_learning_rate = 1e-3                                                          # 后面的分类层的学习率
        self.weight_decay = 0.01                                                                # 权重衰减因子
        self.warmup_proportion = 0.1                                                            # Proportion of training to perform linear learning rate warmup for.
        self.k_fold = 5
        # logging
        self.is_logging2file = True
        self.logging_dir = absdir + '/logging' + '/' + self.task + '/' + self.models_name
        # save
        self.load_save_model = False
        self.save_path = absdir + '/model_saved' + '/' + self.task + '/' + self.models_name
        self.seed = 20
        # 增强数据
        self.data_augment = True
        # [train_augment, train_dev_augment, chip2019, new_category]
        # predict下使用train_augment, full_train下使用train_dev_augment
        self.data_augment_method = ['train_dev_augment', 'new_category', 'chip2019']
        # Bert的后几层加权输出
        self.weighted_layer_tag = False
        self.weighted_layer_num = 12
        # 拼接max_pooling和avg_pooling
        self.pooling_tag = False
        # 计算loss的方法
        self.loss_method = 'binary'  # [ binary, cross_entropy, focal_loss, ghmc]
        # 说明
        self.z_test = 'multi-sample-drop:1'
        # 差分学习率
        self.diff_learning_rate = False
        # multi-task
        self.multi_loss_tag = False
        self.multi_loss_weight = 0.1
        self.multi_class_list = []   # 第二任务标签
        self.multi_num_labels = 0    # 第二任务标签 数量
        # train pattern
        self.pattern = 'full_train'   # [predict, full_train, k_fold, k_volt, k_volt_submit]
        # preprocessing
        self.stop_word_valid = True
        self.medicine_valid = False
        self.symptom_valid = False
        self.medicine_replace_word = ''
        self.symptom_replace_word = ''
        # prob
        self.out_prob = True
        # variety_save
        self.model_tag = "ernie"           #修改标签，以确保不覆盖
        self.variety_save_path = "./variety/variety.csv"


def thucNews_task(config):

    if config.device.type == 'cuda':
        torch.cuda.set_device(config.device_id)

    random_seed(config.seed)

    tokenizer = BertTokenizer.from_pretrained(config.tokenizer_file,
                                              do_lower_case=config.do_lower_case)
    processor = TryDataProcessor(config)
    config.class_list = processor.get_labels()
    config.num_labels = len(config.class_list)
    if config.multi_loss_tag:
        config.multi_class_list = processor.get_second_task_labels()
        config.multi_num_labels = len(config.multi_class_list)

    train_examples = processor.get_train_examples(config.data_dir)
    dev_examples = processor.get_dev_examples(config.data_dir)
    # test_examples = processor.get_test_examples(config.data_dir)
    test_examples = copy.deepcopy(dev_examples)
    augment_examples = processor.read_data_augment(config.data_augment_method)

    cur_model = MODEL_CLASSES[config.use_model]
    model = cur_model(config)

    logging.info("self config %s", config_to_json_string(config))

    if config.load_save_model:
        model_load(config, model, device='cpu')

    model_example, dev_evaluate, predict_label = cross_validation(
        config=config,
        train_examples=train_examples,
        dev_examples=dev_examples,
        model=model,
        tokenizer=tokenizer,
        pattern=config.pattern,
        train_enhancement=augment_examples if config.data_augment else None,
        test_examples=test_examples)
    logging.info("dev_evaluate: {}".format(dev_evaluate))

    # val_variety = list([dev_evaluate]).extend(list(predict_label))       #第一行是正确率
    # if os.path.exists(config.variety_save_path):   #保存predict文件
    #     df_variety = pd.read_csv(config.variety_save_path)
    #     df_variety[config.model_tag] = val_variety
    # else:
    #     df_variety = pd.DataFrame({config.model_tag: val_variety})
    # df_variety.to_csv(config.variety_save_path, index=False)
    
    # volt for predict
    if config.pattern == 'k_volt':
        dev_labels = processor.get_dev_labels(config.data_dir)
        final_pred = k_fold_volt_predict(predict_label)
        final_acc = metrics.accuracy_score(dev_labels, final_pred)
        logging.info('final acc is :{}'.format(final_acc))
    elif config.pattern == 'full_train':
        model_save(config, model_example)

    return dev_evaluate


if __name__ == '__main__':

    config = NewsConfig()
    logging_filename = None
    if config.is_logging2file is True:
        file = time.strftime('%Y-%m-%d_%H-%M-%S') + '.log'
        logging_filename = os.path.join(config.logging_dir, file)
        if not os.path.exists(config.logging_dir):
            os.makedirs(config.logging_dir)

    logging.basicConfig(filename=logging_filename, format='%(levelname)s: %(message)s', level=logging.INFO)
    # 参数搜索
    # search_result = {}
    # diff_learning_rate_status = [False, True]
    # rand_seed = [1, 2, 3, 5, 20, 40, 60, 80, 330, 1874, 1996, 2020]
    # for diff_s in diff_learning_rate_status:
    #     config.diff_learning_rate = diff_s
    #     diff_learning_rate_result = {}
    #     for seed in rand_seed:
    #         config.seed = seed
    #         logging.info("diff_learning_rate: {} seed: {}".format(diff_s, seed))
    #         dev_evaluate = thucNews_task(config)
    #         logging.info("diff_learning_rate: {} seed: {} acc: {}".format(diff_s, seed, dev_evaluate))
    #         diff_learning_rate_result[seed] = dev_evaluate
    #     values = np.array(list(diff_learning_rate_result.values()))
    #     diff_learning_rate_result['mean'] = values.mean()
    #     diff_learning_rate_result['std'] = values.std()
    #     search_result[diff_s] = diff_learning_rate_result
    # logging.info(json.dumps(search_result))
    thucNews_task(config)



import yaml, ipdb

def load_config(args):
    # base config
    base_configuration = load_base_config()

    # load special config for each model
    model = args['model']
    config_path = f'config/{model}.yaml'
    print(f'[!] load configuration: {config_path}')

    with open(config_path) as f:
        configuration = yaml.load(f, Loader=yaml.FullLoader)
        new_config = {}
        for key, value in configuration.items():
            if key in ['train', 'test', 'inference']:
                if args['mode'] == key:
                    new_config.update(value)
            else:
                new_config[key] = value
        configuration = new_config

    # update and append the special config for base config
    base_configuration.update(configuration)
    configuration = base_configuration

    # load by lang
    args['lang'] = configuration['datasets'][args['dataset']]
    configuration['tokenizer'] = configuration['tokenizer'][args['lang']]
    configuration['pretrained_model'] = configuration['pretrained_model'][args['lang']]
    return configuration

def load_base_config():
    config_path = f'config/base.yaml'
    with open(config_path) as f:
        configuration = yaml.load(f, Loader=yaml.FullLoader)
    print(f'[!] load base configuration: {config_path}')
    return configuration

def load_deploy_config(api_name):
    # base config
    args = load_base_config()

    # load deploy parameters from base config
    args.update(args['deploy'])
    args.update(args['deploy'][api_name])
    model = args['model']
    config_path = f'config/{model}.yaml'
    with open(config_path) as f:
        configuration = yaml.load(f, Loader=yaml.FullLoader)
    print(f'[!] load configuration: {config_path}')
    
    # update and append the special config for base config
    args.update(configuration)

    # load by lang
    args['lang'] = args['datasets'][args['dataset']]
    args['tokenizer'] = args['tokenizer'][args['lang']]
    args['pretrained_model'] = args['pretrained_model'][args['lang']]

    # mode (test: single GPU)
    args['mode'] = 'test'
    return args
